"""
Utilidades para manejo de categorías por usuario.
Proporciona funciones para la transición gradual de categorías globales a categorías por usuario.
"""

from typing import List, Dict, Any, Optional, Tuple
from django.contrib.auth import get_user_model
from .models import Category, UserExpenseCategory, UserIncomeCategory
from income.models import IncomeCategory

User = get_user_model()


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


def get_user_categories_with_details(user: User) -> Dict[str, Dict[str, Any]]:
    """
    Obtiene un diccionario con todas las categorías del usuario (gastos e ingresos)
    incluyendo su descripción, ejemplos y color.

    Returns:
        dict: Diccionario donde cada clave es el nombre de la categoría y cada valor
              es otro diccionario con 'description', 'examples', 'color', 'type'.
    """
    try:
        result = {}

        # Obtener categorías de gastos del usuario
        expense_categories = UserExpenseCategory.objects.filter(user=user).values(
            'name', 'description', 'examples', 'color')

        # Obtener categorías de ingresos del usuario
        income_categories = UserIncomeCategory.objects.filter(user=user).values(
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
        print(f"Error al obtener categorías del usuario con detalles: {e}")
        return {}


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
        # Normalizar el nombre (primera letra mayúscula, resto como está)
        normalized_name = name.strip().capitalize()

        # Intentar obtener la categoría existente
        try:
            category = UserExpenseCategory.objects.get(
                user=user, name=normalized_name)

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
        # Normalizar el nombre (primera letra mayúscula, resto como está)
        normalized_name = name.strip().capitalize()

        # Intentar obtener la categoría existente
        try:
            category = UserIncomeCategory.objects.get(
                user=user, name=normalized_name)

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
