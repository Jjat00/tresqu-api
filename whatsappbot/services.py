# services.py
import logging
from openai import OpenAI
from typing import List, Dict, Any
import asyncio
from datetime import datetime
from langchain.memory import ConversationBufferWindowMemory
from whatsappbot.utils import fetch_last_messages

from django.conf import settings
from users.models import User

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.tools import tool

# Importamos las herramientas de telegrambot ya que son genéricas
# para nuestro caso de uso
from telegrambot.tools import (
    get_current_date,
    parse_expense,
    is_greeting,
    create_expense,
    parse_expenses,
    get_or_create_category,
    parse_relative_date,
    update_expense,
    delete_expense,
    get_expenses_by_user,
    get_expense_by_id,
    search_expenses_by_text,
    get_expenses_by_category,
    get_top_categories,
    # Herramientas de ingresos
    parse_income,
    parse_incomes,
    create_income,
    update_income,
    delete_income,
    get_incomes_by_user,
    get_income_by_id,
    search_incomes_by_text,
    get_incomes_by_category,
    get_top_income_categories,
    get_or_create_income_category,
)

from whatsappbot.utils import get_existing_categories, get_categories_with_details, get_existing_income_categories

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

llm = ChatOpenAI(model="gpt-4.1", temperature=0.1,
                 api_key=settings.OPENAI_API_KEY)

# Cliente de OpenAI para posibles integraciones futuras
openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)


async def build_memory(user_id: int) -> ConversationBufferWindowMemory:
    """Construye la memoria de conversación para el usuario"""
    mem = ConversationBufferWindowMemory(
        k=10,
        memory_key="history",
        return_messages=True,
        output_key="output"
    )
    async for msg in fetch_last_messages(user_id):
        mem.chat_memory.add_message(msg)
    return mem


def build_agent(tools, prompt, memory) -> AgentExecutor:
    """Crea un agente con las herramientas, prompt y memoria dadas"""
    agent = create_openai_tools_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        return_intermediate_steps=True,
        verbose=True,
    )


def make_create_expense_tool(user_external_id: str):
    """
    Crea una herramienta para registrar gastos específica para un usuario
    """
    @tool
    async def create_expense_for_user(
        amount: float,
        currency: str,
        category: str,
        spent_at: str | None = None,
        note: str | None = "",
    ) -> str:
        """Registra un gasto en la base de datos y confirma el registro."""
        # Invocación asíncrona
        return await create_expense.ainvoke(
            {
                "user_external_id": user_external_id,
                "amount": amount,
                "currency": currency,
                "category": category,
                "spent_at": spent_at,
                "note": note,
            }
        )

    return create_expense_for_user


def make_create_income_tool(user_external_id: str):
    """
    Crea una herramienta para registrar ingresos específica para un usuario
    """
    @tool
    async def create_income_for_user(
        amount: float,
        currency: str,
        category: str,
        received_at: str | None = None,
        note: str | None = "",
        category_description: str | None = None,
        category_example: str | None = None,
        category_color: str | None = None
    ) -> str:
        """Registra un ingreso en la base de datos y confirma el registro."""
        # Primero crear la categoría de ingreso si no existe de forma asíncrona
        await get_or_create_income_category.ainvoke({
            "name": category,
            "description": category_description,
            "example": category_example,
            "color": category_color
        })

        # Luego registrar el ingreso de forma asíncrona
        return await create_income.ainvoke(
            {
                "user_external_id": user_external_id,
                "amount": amount,
                "currency": currency,
                "category": category,
                "received_at": received_at,
                "note": note,
            }
        )

    return create_income_for_user


async def process_message(user: User, raw_text: str) -> str:
    """
    Procesa un mensaje de WhatsApp y devuelve la respuesta del agente
    """
    try:
        # 1. herramientas (incluye create_expense closure)
        @tool
        async def get_current_date_for_user() -> str:
            """Obtiene la fecha actual en el formato YYYY-MM-DD, considerando la zona horaria del usuario."""
            try:
                return await get_current_date.ainvoke({
                    "user_external_id": user.external_id
                })
            except Exception as e:
                logger.error(f"Error al obtener la fecha actual: {e}")
                return datetime.now().strftime("%Y-%m-%d")

        @tool
        async def parse_relative_date_for_user(date_text: str) -> str:
            """Convierte referencias temporales relativas a fechas específicas."""
            try:
                return await parse_relative_date.ainvoke({
                    "date_text": date_text,
                    "user_external_id": user.external_id
                })
            except Exception as e:
                logger.error(f"Error al analizar fecha relativa: {e}")
                return datetime.now().strftime("%Y-%m-%d")

        # Crear versiones asíncronas de las herramientas básicas
        basic_tools = [
            get_current_date_for_user,
            parse_expense,
            parse_income,
            is_greeting,
            make_create_expense_tool(user.external_id),
            make_create_income_tool(user.external_id),
            parse_expenses,
            parse_incomes,
            get_or_create_category,
            parse_relative_date_for_user,
            update_expense,
            update_income,
            delete_expense,
            delete_income,
            get_expense_by_id,
            get_income_by_id,
        ]

        # Agregar herramientas adicionales que requieren user_external_id
        @tool
        async def get_user_expenses(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
            """Obtiene todos los gastos del usuario en un rango de fechas opcional."""
            try:
                return await get_expenses_by_user.ainvoke({
                    "user_external_id": user.external_id,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(f"Error al obtener gastos del usuario: {e}")
                return []

        @tool
        async def get_user_incomes(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
            """Obtiene todos los ingresos del usuario en un rango de fechas opcional."""
            try:
                return await get_incomes_by_user.ainvoke({
                    "user_external_id": user.external_id,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(f"Error al obtener ingresos del usuario: {e}")
                return []

        @tool
        async def search_expenses(search_text: str) -> List[Dict[str, Any]]:
            """Busca gastos que coincidan con el texto de búsqueda."""
            try:
                return await search_expenses_by_text.ainvoke({
                    "user_external_id": user.external_id,
                    "search_text": search_text
                })
            except Exception as e:
                logger.error(f"Error al buscar gastos: {e}")
                return []

        @tool
        async def search_incomes(search_text: str) -> List[Dict[str, Any]]:
            """Busca ingresos que coincidan con el texto de búsqueda."""
            try:
                return await search_incomes_by_text.ainvoke({
                    "user_external_id": user.external_id,
                    "search_text": search_text
                })
            except Exception as e:
                logger.error(f"Error al buscar ingresos: {e}")
                return []

        @tool
        async def get_category_expenses(category: str, start_date: str | None = None, end_date: str | None = None) -> Dict[str, Any]:
            """Obtiene los gastos de una categoría específica en un rango de fechas."""
            try:
                return await get_expenses_by_category.ainvoke({
                    "user_external_id": user.external_id,
                    "category": category,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(f"Error al obtener gastos por categoría: {e}")
                return {'error': str(e)}

        @tool
        async def get_category_incomes(category: str, start_date: str | None = None, end_date: str | None = None) -> Dict[str, Any]:
            """Obtiene los ingresos de una categoría específica en un rango de fechas."""
            try:
                return await get_incomes_by_category.ainvoke({
                    "user_external_id": user.external_id,
                    "category": category,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(f"Error al obtener ingresos por categoría: {e}")
                return {'error': str(e)}

        @tool
        async def get_top_expense_categories(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
            """Obtiene las categorías con mayores gastos en un rango de fechas."""
            try:
                return await get_top_categories.ainvoke({
                    "user_external_id": user.external_id,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(f"Error al obtener top categorías de gastos: {e}")
                return {'error': str(e)}

        @tool
        async def get_top_income_categories_for_user(start_date: str | None = None, end_date: str | None = None) -> List[Dict[str, Any]]:
            """Obtiene las categorías con mayores ingresos en un rango de fechas."""
            try:
                return await get_top_income_categories.ainvoke({
                    "user_external_id": user.external_id,
                    "start_date": start_date,
                    "end_date": end_date
                })
            except Exception as e:
                logger.error(
                    f"Error al obtener top categorías de ingresos: {e}")
                return {'error': str(e)}

        # Agregar herramientas adicionales al conjunto
        additional_tools = [
            get_user_expenses, get_user_incomes,
            search_expenses, search_incomes,
            get_category_expenses, get_category_incomes,
            get_top_expense_categories, get_top_income_categories_for_user
        ]

        # Combinar todas las herramientas
        async_tools = basic_tools + additional_tools

        # 2. Obtener las categorías con sus detalles para construir el prompt
        categories_with_details = await get_categories_with_details()

        # Construir información detallada de categorías para el prompt
        expense_categories_info = []
        income_categories_info = []

        for name, details in categories_with_details.items():
            category_info = f"- {name}: {details['description']} Ejemplos: {details['examples']}"
            if details.get('type') == 'income':
                income_categories_info.append(category_info)
            else:
                expense_categories_info.append(category_info)

        # Ordenar alfabéticamente las categorías para el prompt
        expense_categories_info.sort()
        income_categories_info.sort()

        # Crear secciones separadas para el prompt
        expenses_detailed_str = "CATEGORÍAS DE GASTOS:\n" + \
            "\n".join(expense_categories_info)
        incomes_detailed_str = "CATEGORÍAS DE INGRESOS:\n" + \
            "\n".join(income_categories_info)

        # Combinar ambas secciones
        categories_detailed_str = f"{expenses_detailed_str}\n\n{incomes_detailed_str}"

        # Obtener solo la lista de nombres para mantener la compatibilidad
        existing_expense_categories = await get_existing_categories()
        existing_income_categories = await get_existing_income_categories()

        expense_categories_str = 'Gastos: ' + \
            ', '.join(existing_expense_categories)
        income_categories_str = 'Ingresos: ' + \
            ', '.join(existing_income_categories)

        # Usar el mismo prompt detallado que en el bot de Telegram
        prompt = ChatPromptTemplate.from_messages([
            ("system", f"""
            Eres un asistente financiero experto en clasificar gastos e ingresos, te llamas Tresqu.
            
            Categorías disponibles para gastos: {expense_categories_str}
            Categorías disponibles para ingresos: {income_categories_str}

            INSTRUCCIONES:
            1. Si detectas un saludo corto ⇒ usa is_greeting y responde con un saludo.
            
            PARA GASTOS:
            2. Si hay UN solo gasto ⇒ usa parse_expense y luego create_expense.
            3. Si el mensaje contiene MÁS de un gasto (separado por "y", "," o ";"…) ⇒
                3.1 Usa parse_expenses.
                3.2 Recorre cada elemento del array devuelto y llama a create_expense
                    para cada gasto individual.
            4. Si identificas referencias temporales (ayer, el sábado, etc.) ⇒ usa parse_relative_date_for_user
               para convertirlas en fechas específicas antes de crear el gasto.
               IMPORTANTE: cuando el usuario menciona un día de la semana (ej: "el sábado gasté"),
               asume que se refiere al día más reciente en el pasado, no al próximo.
            5. Si falta fecha ⇒ usa get_current_date_for_user.
            6. Si falta moneda ⇒ create_expense asignará la moneda por defecto.
            
            PARA INGRESOS:
            7. Si hay un solo ingreso ⇒ usa parse_income y luego create_income.
            8. Si el mensaje contiene MÁS de un ingreso (separado por "y", "," o ";"…) ⇒
                8.1 Usa parse_incomes.
                8.2 Recorre cada elemento del array devuelto y llama a create_income
                    para cada ingreso individual.
            9. Si identificas referencias temporales para ingresos ⇒ usa parse_relative_date_for_user
               para convertirlas en fechas específicas antes de crear el ingreso.
            10. Si falta fecha ⇒ usa get_current_date_for_user.
            11. Si falta moneda ⇒ create_income asignará la moneda por defecto.
            
            PARA AMBOS:
            12. Si el mensaje pregunta algo responde de acuerdo al historial de mensajes.
            13. Clasifica el movimiento en una de las categorías proporcionadas:
                13.1 PRIMERO: Intenta usar una categoría existente de la lista proporcionada
                     - Revisa cuidadosamente las categorías disponibles
                     - Busca la categoría más apropiada basada en la descripción y ejemplos
                     - Si hay una categoría similar, úsala en lugar de crear una nueva
                13.2 SOLO SI ES NECESARIO: Si ninguna categoría existente es adecuada:
                     - Usa get_or_create_category o get_or_create_income_category según corresponda
                     - Proporciona nombre, descripción, ejemplos y color
                     - Asegúrate de que la nueva categoría sea realmente necesaria
                13.3 Si dudas entre dos categorías existentes:
                     - Elige la que mejor se adapte según los ejemplos proporcionados
                     - Prefiere categorías más generales sobre específicas
                     - Si hay una categoría "Otros" o similar, úsala como último recurso
            14. Si no se especifica fecha, usa get_current_date_for_user para la fecha actual
            
            EDICIÓN Y ELIMINACIÓN:
            16. Si el usuario quiere editar un gasto:
                16.1 Si menciona un ID específico ⇒ usa get_expense_by_id para verificar que existe
                16.2 Si no menciona ID pero describe el gasto ⇒ usa search_expenses_by_text
                16.3 Si encuentra el gasto, usa update_expense para modificarlo
                16.4 Si no encuentra el gasto, pide más detalles
            17. Si el usuario quiere eliminar un gasto:
                17.1 Si menciona un ID específico ⇒ usa get_expense_by_id para verificar que existe
                17.2 Si no menciona ID pero describe el gasto ⇒ usa search_expenses_by_text
                17.3 Si encuentra el gasto, usa delete_expense para eliminarlo
                17.4 Si no encuentra el gasto, pide más detalles
            18. Si el usuario quiere editar un ingreso:
                18.1 Si menciona un ID específico ⇒ usa get_income_by_id para verificar que existe
                18.2 Si no menciona ID pero describe el ingreso ⇒ usa search_incomes_by_text
                18.3 Si encuentra el ingreso, usa update_income para modificarlo
                18.4 Si no encuentra el ingreso, pide más detalles
            19. Si el usuario quiere eliminar un ingreso:
                19.1 Si menciona un ID específico ⇒ usa get_income_by_id para verificar que existe
                19.2 Si no menciona ID pero describe el ingreso ⇒ usa search_incomes_by_text
                19.3 Si encuentra el ingreso, usa delete_income para eliminarlo
                19.4 Si no encuentra el ingreso, pide más detalles
            
            CONSULTAS:
            20. Si el usuario hace consultas sobre sus gastos o ingresos:
                20.1 Para consultar por ID específico:
                    - Usa get_expense_by_id o get_income_by_id según corresponda
                20.2 Para consultar por categoría en un período:
                    - Usa get_category_expenses o get_category_incomes según corresponda
                    - Si no se especifica fecha, usa get_current_date_for_user para la fecha actual
                    - Si se menciona "este mes", calcula el primer día del mes actual
                20.3 Para consultar las categorías con mayores movimientos:
                    - Usa get_top_expense_categories o get_top_income_categories_for_user según corresponda
                    - Si no se especifica fecha, muestra todas las categorías
                    - Ordena los resultados de mayor a menor
                20.4 Para búsquedas semánticas:
                    - Usa search_expenses o search_incomes para buscar movimientos similares
                    - Estas funciones usan embeddings para encontrar resultados semánticamente relacionados
                    - Por ejemplo, buscar "comida" encontrará "restaurante", "almuerzo", "cena"
                    - Por ejemplo, buscar "transporte" encontrará "taxi", "uber", "metro"
                    - NO uses estas funciones para consultas de período (esta semana, este mes, etc.)
                20.5 Para consultas de período:
                    - Si el usuario pregunta "cuánto gasté/ingresé esta semana/mes/etc":
                        * Usa get_top_expense_categories o get_top_income_categories_for_user
                        * Calcula el total sumando los montos de todas las categorías
                        * Muestra un resumen por categoría y el total general
                20.6 Para listar todos los movimientos del usuario:
                    * Usa get_user_expenses o get_user_incomes según corresponda
                    * Si no se especifica fecha, muestra todos los movimientos
                    * Si se especifica un rango de fechas, filtra por ese rango

            CREACIÓN DE CATEGORÍAS DE INGRESOS:
            21. Al crear nuevas categorías de ingresos con get_or_create_income_category:
                21.1 SOLO crear una nueva categoría si:
                    - No existe una categoría similar en la lista proporcionada
                    - El ingreso no puede clasificarse en ninguna categoría existente
                    - La categoría es realmente necesaria y no es un caso aislado
                21.2 Proporciona siempre estos parámetros:
                    * name: Nombre de la categoría
                    * description: Descripción breve de la categoría (qué tipo de ingresos incluye)
                    * example: Ejemplos concretos de ingresos que pertenecen a esta categoría
                    * color: Color hexadecimal (#RRGGBB) que represente visualmente la categoría
                21.3 Al registrar un ingreso con create_income_for_user, usa los parámetros adicionales:
                    * category_description: para la descripción de la categoría
                    * category_example: para los ejemplos de la categoría
                    * category_color: para el color de la categoría
                21.4 Estos campos son importantes para que el usuario pueda entender mejor cada categoría

            IMPORTANTE:
            - Siempre determina correctamente si el mensaje se refiere a un GASTO o a un INGRESO
            - Para gastos, usa spent_at como fecha
            - Para ingresos, usa received_at como fecha
            - La palabra clave para detectar ingresos es: recibí, me pagaron, ingresé, gané, etc.
            - La palabra clave para detectar gastos es: gasté, pagué, compré, etc.
            - Si no puedes determinar si es gasto o ingreso, pregúntale al usuario
            - Las herramientas ya incluyen el ID del usuario actual
            - Siempre que generes reportes de gastos o ingresos usa negrita con un asterisco (*) para el formato en negrita. ejemplo: "*Categoría*: 100 COP"
            - Puedes responder también con cursiva, ejemplo: "_Categoría_: 100 COP", usalo cuando sea necesario.
            - Los nombres de las categorías nuevas SIEMPRE deben crearse en el mismo idioma que el usuario está utilizando
            - Las descripciones, ejemplos y notas de gastos/ingresos SIEMPRE deben escribirse en el mismo idioma del usuario
            - PRIORIZA SIEMPRE el uso de categorías existentes sobre la creación de nuevas
            - Si hay una categoría "Otros" o similar, úsala para casos que no encajan perfectamente en otras categorías

            COLORES PARA CATEGORÍAS DE GASTOS:
            - Si necesitas crear una categoría nueva, elige un color hexadecimal (#RRGGBB) que sea visualmente agradable
            - Usa colores que tengan buen contraste y sean coherentes con la temática de la categoría
            - Ejemplos: azul (#1E3A8A) para categorías relacionadas con servicios, verde (#10B981) para alimentación, 
              naranja (#F97316) para transporte, rojo (#DC2626) para préstamos, etc.
            - Asegúrate de que los colores sean atractivos visualmente

            Responde de manera cool, eres joven. 
            Puedes usar emojis y gifs.
            Puedes hacer chistes y bromas si el usuario lo pide.
            Puedes dar consejos.
            Siempre debes mencionar el movimiento registrado, su categoria y la fecha.
            Siempre debes responder en el mismo idioma que el usuario.
            De vez en cuando recuerdale a la gente que pueden ver su dashboard en https://tresqu.com/
            """),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        # 3. memoria
        memory = await build_memory(user.id)

        # 4. construir agente configurado para ejecución asíncrona
        agent = create_openai_tools_agent(llm, async_tools, prompt)
        agent_executor = AgentExecutor(
            agent=agent,
            tools=async_tools,
            memory=memory,
            return_intermediate_steps=True,
            verbose=True,
        )

        # 5. ejecutar agente de forma asíncrona directamente con timeout
        try:
            result = await asyncio.wait_for(
                agent_executor.ainvoke(
                    {"input": raw_text, "history": memory.chat_memory.messages}
                ),
                timeout=120.0  # 120 segundos de timeout
            )
            return result["output"]
        except asyncio.TimeoutError:
            logger.error("Timeout al procesar mensaje")
            return "Lo siento, la operación tomó demasiado tiempo. Por favor, intenta de nuevo con un mensaje más corto o específico."

    except Exception as e:
        logger.exception(f"Error al procesar mensaje: {e}")
        return "Lo siento, hubo un error al procesar tu mensaje. ¿Puedes intentarlo de nuevo?"
