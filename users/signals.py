from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import User


@receiver(post_save, sender=User)
def assign_trial_subscription(sender, instance, created, **kwargs):
    """
    Asigna automáticamente el plan premium de prueba a los nuevos usuarios
    """
    if created:
        # Solo asignar el plan de prueba si es un usuario nuevo
        instance.assign_premium_trial()


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
