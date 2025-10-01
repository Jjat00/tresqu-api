from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import User


@receiver(post_save, sender=User)
def assign_basic_plan_to_new_users(sender, instance, created, **kwargs):
    """
    Asigna automáticamente el plan básico a los nuevos usuarios
    """
    if created:
        # Asignar plan básico por defecto a usuarios nuevos
        instance.assign_basic_plan_if_none()


@receiver(post_save, sender=User)
def assign_default_categories(sender, instance, created, **kwargs):
    """
    Asigna automáticamente todas las categorías predefinidas a los nuevos usuarios
    """
    if created:
        # Importar aquí para evitar dependencias circulares
        from categories.models import UserExpenseCategory, UserIncomeCategory

        # Obtener categorías predefinidas
        predefined_expense_categories = UserExpenseCategory.PREDEFINED_CATEGORIES
        predefined_income_categories = UserIncomeCategory.PREDEFINED_CATEGORIES

        # Crear categorías de gastos por usuario
        expense_categories_created = []
        for cat_name in predefined_expense_categories:
            user_expense_cat, created_cat = UserExpenseCategory.objects.get_or_create(
                user=instance,
                name=cat_name,
                defaults={
                    'description': UserExpenseCategory.get_default_description(cat_name),
                    'examples': UserExpenseCategory.get_default_examples(cat_name),
                    'color': UserExpenseCategory.get_default_color(cat_name),
                    'is_default': True
                }
            )
            if created_cat:
                expense_categories_created.append(user_expense_cat.name)

        # Crear categorías de ingresos por usuario
        income_categories_created = []
        for cat_name in predefined_income_categories:
            user_income_cat, created_cat = UserIncomeCategory.objects.get_or_create(
                user=instance,
                name=cat_name,
                defaults={
                    'description': UserIncomeCategory.get_default_description(cat_name),
                    'example': UserIncomeCategory.get_default_example(cat_name),
                    'color': UserIncomeCategory.get_default_color(cat_name),
                    'is_default': True
                }
            )
            if created_cat:
                income_categories_created.append(user_income_cat.name)

        # Log para debugging
        print(f"✅ Usuario {instance.username} creado con {len(expense_categories_created)} categorías de gastos y {len(income_categories_created)} categorías de ingresos")
        print(
            f"   Gastos: {expense_categories_created[:5]}{'...' if len(expense_categories_created) > 5 else ''}")
        print(
            f"   Ingresos: {income_categories_created[:5]}{'...' if len(income_categories_created) > 5 else ''}")


# ========================================
# SIGNALS PARA CONTADORES MENSUALES
# ========================================

@receiver(post_save, sender='income.Income')
def increment_monthly_income_count(sender, instance, created, **kwargs):
    """
    Incrementa el contador mensual de ingresos cuando se crea un nuevo ingreso
    """
    if created and instance.user:
        from .models import MonthlyUsage
        monthly_usage = MonthlyUsage.get_current_usage(instance.user)
        monthly_usage.increment_incomes()


@receiver(post_delete, sender='income.Income')
def decrement_monthly_income_count(sender, instance, **kwargs):
    """
    Decrementa el contador mensual de ingresos cuando se elimina un ingreso
    """
    if instance.user:
        from .models import MonthlyUsage
        # Obtener el uso mensual del mes en que se creó el ingreso
        created_date = instance.created_at
        try:
            monthly_usage = MonthlyUsage.objects.get(
                user=instance.user,
                year=created_date.year,
                month=created_date.month
            )
            monthly_usage.decrement_incomes()
        except MonthlyUsage.DoesNotExist:
            # Si no existe el registro mensual, no hacer nada
            pass


@receiver(post_save, sender='expenses.Expense')
def increment_monthly_expense_count(sender, instance, created, **kwargs):
    """
    Incrementa el contador mensual de gastos cuando se crea un nuevo gasto
    """
    if created and instance.user:
        from .models import MonthlyUsage
        monthly_usage = MonthlyUsage.get_current_usage(instance.user)
        monthly_usage.increment_expenses()


@receiver(post_delete, sender='expenses.Expense')
def decrement_monthly_expense_count(sender, instance, **kwargs):
    """
    Decrementa el contador mensual de gastos cuando se elimina un gasto
    """
    if instance.user:
        from .models import MonthlyUsage
        # Obtener el uso mensual del mes en que se creó el gasto
        created_date = instance.created_at
        try:
            monthly_usage = MonthlyUsage.objects.get(
                user=instance.user,
                year=created_date.year,
                month=created_date.month
            )
            monthly_usage.decrement_expenses()
        except MonthlyUsage.DoesNotExist:
            # Si no existe el registro mensual, no hacer nada
            pass
