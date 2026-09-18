"""
Utilidades para manejo de categorías por usuario.
Proporciona funciones para la transición gradual de categorías globales a categorías por usuario.
"""

from typing import List, Dict, Any, Optional, Tuple, Union
from django.db.models import QuerySet
from django.contrib.auth import get_user_model
from .models import UserExpenseCategory, UserIncomeCategory, Category
from income.models import IncomeCategory
from users.models import User


# Conectores que en español van en minúscula dentro de un nombre propio.
# ``str.title()`` los mayusculiza ("Salario o Trabajo Fijo" → "Salario O Trabajo
# Fijo"), lo que rompía cualquier comparación exacta contra las categorías
# predefinidas y ensuciaba las creadas por el agente.
_CATEGORY_NAME_LOWERCASE_WORDS = {
    'a', 'al', 'con', 'de', 'del', 'e', 'el', 'en', 'la', 'las', 'lo', 'los',
    'o', 'para', 'por', 'sin', 'sobre', 'u', 'un', 'una', 'y',
}


def normalize_category_name(name: str) -> str:
    """Normaliza el nombre de una categoría respetando los conectores.

    Capitaliza cada palabra salvo los conectores en español, que quedan en
    minúscula cuando no abren el nombre. Así "salario o trabajo fijo" y
    "SALARIO O TRABAJO FIJO" producen ambos "Salario o Trabajo Fijo", que es
    exactamente como se guardan las categorías predefinidas.
    """
    words = (name or "").strip().split()
    normalized = []
    for index, word in enumerate(words):
        lowered = word.lower()
        if index > 0 and lowered in _CATEGORY_NAME_LOWERCASE_WORDS:
            normalized.append(lowered)
        else:
            normalized.append(lowered[:1].upper() + lowered[1:])
    return " ".join(normalized)


# FUNCIONES PRINCIPALES PARA CATEGORÍAS POR USUARIO

def get_user_expense_categories(user: User) -> List[str]:
    """
    Obtiene la lista de nombres de categorías de gastos para un usuario específico.
    """
    try:
        categories = UserExpenseCategory.objects.filter(
            user=user).values_list('name', flat=True)
        return list(categories)
    except Exception as e:
        print(f"Error al obtener categorías de gastos del usuario: {e}")
        return []


def get_user_expense_categories_queryset(user: User) -> QuerySet[UserExpenseCategory]:
    """
    Obtiene el QuerySet de categorías de gastos para un usuario específico.
    Usado por las vistas y serializers.
    """
    try:
        return UserExpenseCategory.objects.filter(user=user).order_by('name')
    except Exception as e:
        print(
            f"Error al obtener QuerySet de categorías de gastos del usuario: {e}")
        return UserExpenseCategory.objects.none()


def get_user_income_categories(user: User) -> List[str]:
    """
    Obtiene la lista de nombres de categorías de ingresos para un usuario específico.
    """
    try:
        categories = UserIncomeCategory.objects.filter(
            user=user).values_list('name', flat=True)
        return list(categories)
    except Exception as e:
        print(f"Error al obtener categorías de ingresos del usuario: {e}")
        return []


def get_user_income_categories_queryset(user: User) -> QuerySet[UserIncomeCategory]:
    """
    Obtiene el QuerySet de categorías de ingresos para un usuario específico.
    Usado por las vistas y serializers.
    """
    try:
        return UserIncomeCategory.objects.filter(user=user).order_by('name')
    except Exception as e:
        print(
            f"Error al obtener QuerySet de categorías de ingresos del usuario: {e}")
        return UserIncomeCategory.objects.none()


def get_user_categories_with_details(user: User) -> Dict[str, Dict[str, Any]]:
    """
    Obtiene un diccionario con todas las categorías del usuario (gastos e ingresos)
    incluyendo su descripción, ejemplos y color.

    Returns:
        dict: Diccionario con 'expense_categories' e 'income_categories'
    """
    try:
        # Obtener categorías de gastos del usuario
        expense_categories = UserExpenseCategory.objects.filter(user=user).values(
            'id', 'name', 'description', 'examples', 'color', 'is_default')

        # Obtener categorías de ingresos del usuario
        income_categories = UserIncomeCategory.objects.filter(user=user).values(
            'id', 'name', 'description', 'example', 'color', 'is_default')

        # Procesar categorías de gastos
        expense_list = []
        for category in expense_categories:
            expense_list.append({
                'id': category['id'],
                'name': category['name'],
                'description': category['description'] or '',
                'examples': category['examples'] or '',
                'color': category['color'],
                'is_default': category['is_default'],
                'type': 'expense'
            })

        # Procesar categorías de ingresos
        income_list = []
        for category in income_categories:
            income_list.append({
                'id': category['id'],
                'name': category['name'],
                'description': category['description'] or '',
                'example': category['example'] or '',
                'color': category['color'],
                'is_default': category['is_default'],
                'type': 'income'
            })

        return {
            'expense_categories': expense_list,
            'income_categories': income_list
        }
    except Exception as e:
        print(f"Error al obtener categorías del usuario con detalles: {e}")
        return {
            'expense_categories': [],
            'income_categories': []
        }


def get_or_create_user_expense_category(
    user: User,
    name: str,
    description: str = None,
    examples: str = None,
    color: str = None
) -> Tuple[UserExpenseCategory, bool]:
    """
    Obtiene o crea una categoría de gastos para un usuario específico.

    Args:
        user: Usuario propietario de la categoría
        name: Nombre de la categoría
        description: Descripción opcional de la categoría
        examples: Ejemplos opcionales de gastos para esta categoría
        color: Color hexadecimal opcional (formato: #RRGGBB)

    Returns:
        Tuple[UserExpenseCategory, bool]: (categoría, fue_creada)
    """
    try:
        # Normalizar el nombre manteniendo el formato título para consistencia
        normalized_name = normalize_category_name(name)

        # Intentar obtener la categoría existente (case-insensitive)
        try:
            category = UserExpenseCategory.objects.get(
                user=user, name__iexact=normalized_name)

            # Actualizar campos si se proporcionan nuevos valores y están vacíos
            updated = False
            if description and not category.description:
                category.description = description
                updated = True
            if examples and not category.examples:
                category.examples = examples
                updated = True
            if color and (not category.color or category.color == '#CCCCCC'):
                category.color = color
                updated = True

            if updated:
                category.save()

            return category, False

        except UserExpenseCategory.DoesNotExist:
            # La categoría no existe, crear una nueva
            category_data = {
                'user': user,
                'name': normalized_name,
                'is_default': False  # Las creadas dinámicamente no son por defecto
            }

            # Agregar campos opcionales si se proporcionan
            if description:
                category_data['description'] = description
            if examples:
                category_data['examples'] = examples
            if color:
                category_data['color'] = color

            # Crear la categoría (el método save() asignará valores por defecto si faltan)
            category = UserExpenseCategory.objects.create(**category_data)
            return category, True

    except Exception as e:
        print(f"Error al obtener/crear categoría de gastos del usuario: {e}")
        # Crear una categoría por defecto en caso de error
        category, created = UserExpenseCategory.objects.get_or_create(
            user=user,
            name="Otros",
            defaults={
                'description': "Categoría por defecto para gastos sin clasificar",
                'examples': "Gastos varios, misceláneos",
                'is_default': False
            }
        )
        return category, created


def get_or_create_user_income_category(
    user: User,
    name: str,
    description: str = None,
    example: str = None,
    color: str = None
) -> Tuple[UserIncomeCategory, bool]:
    """
    Obtiene o crea una categoría de ingresos para un usuario específico.

    Args:
        user: Usuario propietario de la categoría
        name: Nombre de la categoría
        description: Descripción opcional de la categoría
        example: Ejemplo opcional de ingresos para esta categoría
        color: Color hexadecimal opcional (formato: #RRGGBB)

    Returns:
        Tuple[UserIncomeCategory, bool]: (categoría, fue_creada)
    """
    try:
        # Normalizar el nombre manteniendo el formato título para consistencia
        normalized_name = normalize_category_name(name)

        # Intentar obtener la categoría existente (case-insensitive)
        try:
            category = UserIncomeCategory.objects.get(
                user=user, name__iexact=normalized_name)

            # Actualizar campos si se proporcionan nuevos valores y están vacíos
            updated = False
            if description and not category.description:
                category.description = description
                updated = True
            if example and not category.example:
                category.example = example
                updated = True
            if color and (not category.color or category.color == '#CCCCCC'):
                category.color = color
                updated = True

            if updated:
                category.save()

            return category, False

        except UserIncomeCategory.DoesNotExist:
            # La categoría no existe, crear una nueva
            category_data = {
                'user': user,
                'name': normalized_name,
                'is_default': False  # Las creadas dinámicamente no son por defecto
            }

            # Agregar campos opcionales si se proporcionan
            if description:
                category_data['description'] = description
            if example:
                category_data['example'] = example
            if color:
                category_data['color'] = color

            # Crear la categoría (el método save() asignará valores por defecto si faltan)
            category = UserIncomeCategory.objects.create(**category_data)
            return category, True

    except Exception as e:
        print(f"Error al obtener/crear categoría de ingresos del usuario: {e}")
        # Crear una categoría por defecto en caso de error
        category, created = UserIncomeCategory.objects.get_or_create(
            user=user,
            name="Otros Ingresos",
            defaults={
                'description': "Categoría por defecto para ingresos sin clasificar",
                'example': "Ingresos varios, misceláneos",
                'is_default': False
            }
        )
        return category, created


# FUNCIONES HÍBRIDAS PARA COMPATIBILIDAD DURANTE LA TRANSICIÓN

def get_expense_categories_hybrid(user: User = None) -> List[str]:
    """
    Función híbrida que devuelve categorías por usuario si se proporciona usuario,
    o categorías globales como fallback.

    USAR DURANTE LA TRANSICIÓN SOLAMENTE.
    """
    if user:
        return get_user_expense_categories(user)
    else:
        # Fallback a categorías globales
        try:
            return list(Category.objects.values_list('name', flat=True))
        except Exception as e:
            print(f"Error al obtener categorías globales: {e}")
            return []


def get_income_categories_hybrid(user: User = None) -> List[str]:
    """
    Función híbrida que devuelve categorías por usuario si se proporciona usuario,
    o categorías globales como fallback.

    USAR DURANTE LA TRANSICIÓN SOLAMENTE.
    """
    if user:
        return get_user_income_categories(user)
    else:
        # Fallback a categorías globales
        try:
            return list(IncomeCategory.objects.values_list('name', flat=True))
        except Exception as e:
            print(f"Error al obtener categorías globales de ingresos: {e}")
            return []


def get_categories_with_details_hybrid(user: User = None) -> Dict[str, Dict[str, Any]]:
    """
    Función híbrida que devuelve categorías por usuario si se proporciona usuario,
    o categorías globales como fallback.

    USAR DURANTE LA TRANSICIÓN SOLAMENTE.
    """
    if user:
        return get_user_categories_with_details(user)
    else:
        # Fallback a categorías globales (lógica existente)
        try:
            result = {}

            # Obtener categorías de gastos globales
            expense_categories = Category.objects.all().values(
                'name', 'description', 'examples', 'color')

            # Obtener categorías de ingresos globales
            income_categories = IncomeCategory.objects.all().values(
                'name', 'description', 'example', 'color')

            # Procesar categorías de gastos
            for category in expense_categories:
                result[category['name']] = {
                    'description': category['description'] or '',
                    'examples': category['examples'] or '',
                    'color': category['color'],
                    'type': 'expense'
                }

            # Procesar categorías de ingresos
            for category in income_categories:
                result[category['name']] = {
                    'description': category['description'] or '',
                    'examples': category['example'] or '',
                    'color': category['color'],
                    'type': 'income'
                }

            return result
        except Exception as e:
            print(f"Error al obtener categorías globales con detalles: {e}")
            return {}
