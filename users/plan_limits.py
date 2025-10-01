"""
Configuración de límites MENSUALES por plan de suscripción

IMPORTANTE: Todos los límites son POR MES y se reinician automáticamente
cada mes calendario.
"""

# Límites de registros MENSUALES por plan
PLAN_LIMITS = {
    'BASIC': {
        'max_expenses': 20,    # 20 gastos por mes
        'max_incomes': 20,     # 20 ingresos por mes
        'description': 'Plan básico con límites mensuales para nuevos usuarios'
    },
    'PREMIUM': {
        'max_expenses': None,  # Ilimitado por mes
        'max_incomes': None,   # Ilimitado por mes
        'description': 'Plan premium con registros ilimitados por mes'
    },
    'BUSINESS': {
        'max_expenses': None,  # Ilimitado por mes
        'max_incomes': None,   # Ilimitado por mes
        'description': 'Plan empresarial con registros ilimitados por mes'
    }
}


def get_plan_limits(plan_name):
    """
    Obtiene los límites para un plan específico

    Args:
        plan_name (str): Nombre del plan ('BASIC', 'PREMIUM', 'BUSINESS')

    Returns:
        dict: Diccionario con los límites del plan
    """
    return PLAN_LIMITS.get(plan_name, PLAN_LIMITS['BASIC'])


def get_max_expenses(plan_name):
    """
    Obtiene el límite máximo de gastos para un plan

    Args:
        plan_name (str): Nombre del plan

    Returns:
        int or None: Límite de gastos (None = ilimitado)
    """
    limits = get_plan_limits(plan_name)
    return limits.get('max_expenses')


def get_max_incomes(plan_name):
    """
    Obtiene el límite máximo de ingresos para un plan

    Args:
        plan_name (str): Nombre del plan

    Returns:
        int or None: Límite de ingresos (None = ilimitado)
    """
    limits = get_plan_limits(plan_name)
    return limits.get('max_incomes')


def is_unlimited_plan(plan_name):
    """
    Verifica si un plan tiene registros ilimitados

    Args:
        plan_name (str): Nombre del plan

    Returns:
        bool: True si el plan es ilimitado
    """
    limits = get_plan_limits(plan_name)
    return limits.get('max_expenses') is None and limits.get('max_incomes') is None


def get_upgrade_message(plan_name, record_type='expense'):
    """
    Genera un mensaje de actualización personalizado usando los límites reales configurados

    Args:
        plan_name (str): Plan actual del usuario
        record_type (str): Tipo de registro ('expense' o 'income')

    Returns:
        str: Mensaje de actualización
    """
    if plan_name == 'BASIC':
        limits = get_plan_limits(plan_name)

        if record_type == 'expense':
            max_expenses = limits.get('max_expenses', 20)
            return f"Has alcanzado el límite de {max_expenses} gastos mensuales del plan básico. Actualiza a Premium para registros ilimitados y más funciones. El límite se reinicia el próximo mes."
        else:
            max_incomes = limits.get('max_incomes', 20)
            return f"Has alcanzado el límite de {max_incomes} ingresos mensuales del plan básico. Actualiza a Premium para registros ilimitados y más funciones. El límite se reinicia el próximo mes."

    return "Límite mensual alcanzado. Contacta soporte para más información."
