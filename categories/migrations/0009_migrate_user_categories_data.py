# Generated manually for user categories migration
from django.db import migrations


def migrate_categories_to_users(apps, schema_editor):
    """
    Migra las categorías existentes a categorías por usuario siguiendo esta lógica:
    1. Todas las categorías predefinidas se asignan a todos los usuarios
    2. Las categorías personalizadas se asignan solo a usuarios que las han usado
    """
    # Obtener modelos
    Category = apps.get_model('categories', 'Category')
    IncomeCategory = apps.get_model('income', 'IncomeCategory')
    UserExpenseCategory = apps.get_model('categories', 'UserExpenseCategory')
    UserIncomeCategory = apps.get_model('categories', 'UserIncomeCategory')
    User = apps.get_model('users', 'User')
    Expense = apps.get_model('expenses', 'Expense')
    Income = apps.get_model('income', 'Income')

    print("🚀 Iniciando migración de categorías a usuarios...")

    # Categorías predefinidas
    PREDEFINED_EXPENSE_CATEGORIES = [
        'Vivienda', 'Alimentación', 'Transporte y Movilidad', 'Préstamos',
        'Salud y Bienestar', 'Educación y Formación', 'Ropa', 'Cuidado Personal',
        'Compras', 'Entretenimiento y Ocio', 'Viajes y Salidas',
        'Bebidas Alcohólicas y Fiestas', 'Familia y Dependientes', 'Mascotas',
        'Regalos y Donaciones', 'Suscripciones y Membresías'
    ]

    PREDEFINED_INCOME_CATEGORIES = [
        'Salario o Trabajo Fijo', 'Trabajo Independiente o Freelance', 'Negocios o Emprendimientos',
        'Inversiones', 'Alquileres y Activos', 'Regalías y Derechos',
        'Apoyos o Subsidios', 'Premios y Sorteos', 'Venta de Bienes'
    ]

    all_users = User.objects.all()
    print(f"📊 Usuarios encontrados: {all_users.count()}")

    # 1. MIGRAR CATEGORÍAS DE GASTOS
    print("\n💰 Migrando categorías de gastos...")

    all_expense_categories = Category.objects.all()
    predefined_expense_cats = all_expense_categories.filter(
        name__in=PREDEFINED_EXPENSE_CATEGORIES)
    custom_expense_cats = all_expense_categories.exclude(
        name__in=PREDEFINED_EXPENSE_CATEGORIES)

    print(f"  - Categorías predefinidas: {predefined_expense_cats.count()}")
    print(f"  - Categorías personalizadas: {custom_expense_cats.count()}")

    # 1.1 Asignar categorías predefinidas a todos los usuarios
    for user in all_users:
        for category in predefined_expense_cats:
            UserExpenseCategory.objects.get_or_create(
                user=user,
                name=category.name,
                defaults={
                    'color': category.color,
                    'description': category.description or '',
                    'examples': category.examples or '',
                    'metadata': category.metadata,
                    'is_default': True  # Es predefinida
                }
            )

    print(
        f"  ✅ Asignadas {predefined_expense_cats.count()} categorías predefinidas a {all_users.count()} usuarios")

    # 1.2 Asignar categorías personalizadas solo a usuarios que las han usado
    for category in custom_expense_cats:
        # Encontrar usuarios que han usado esta categoría
        users_using_category = Expense.objects.filter(
            category=category
        ).values_list('user', flat=True).distinct()

        users_count = len(users_using_category)
        print(f"  - '{category.name}': usada por {users_count} usuarios")

        # Crear UserExpenseCategory para cada usuario que la usó
        for user_id in users_using_category:
            user = User.objects.get(id=user_id)
            UserExpenseCategory.objects.get_or_create(
                user=user,
                name=category.name,
                defaults={
                    'color': category.color,
                    'description': category.description or '',
                    'examples': category.examples or '',
                    'metadata': category.metadata,
                    'is_default': False  # Es personalizada
                }
            )

    # 2. MIGRAR CATEGORÍAS DE INGRESOS
    print("\n💵 Migrando categorías de ingresos...")

    all_income_categories = IncomeCategory.objects.all()
    predefined_income_cats = all_income_categories.filter(
        name__in=PREDEFINED_INCOME_CATEGORIES)
    custom_income_cats = all_income_categories.exclude(
        name__in=PREDEFINED_INCOME_CATEGORIES)

    print(f"  - Categorías predefinidas: {predefined_income_cats.count()}")
    print(f"  - Categorías personalizadas: {custom_income_cats.count()}")

    # 2.1 Asignar categorías predefinidas a todos los usuarios
    for user in all_users:
        for category in predefined_income_cats:
            UserIncomeCategory.objects.get_or_create(
                user=user,
                name=category.name,
                defaults={
                    'color': category.color,
                    'description': category.description or '',
                    'example': category.example or '',
                    'metadata': category.metadata,
                    'is_default': True  # Es predefinida
                }
            )

    print(
        f"  ✅ Asignadas {predefined_income_cats.count()} categorías predefinidas a {all_users.count()} usuarios")

    # 2.2 Asignar categorías personalizadas solo a usuarios que las han usado
    for category in custom_income_cats:
        # Encontrar usuarios que han usado esta categoría
        users_using_category = Income.objects.filter(
            category=category
        ).values_list('user', flat=True).distinct()

        users_count = len(users_using_category)
        print(f"  - '{category.name}': usada por {users_count} usuarios")

        # Crear UserIncomeCategory para cada usuario que la usó
        for user_id in users_using_category:
            user = User.objects.get(id=user_id)
            UserIncomeCategory.objects.get_or_create(
                user=user,
                name=category.name,
                defaults={
                    'color': category.color,
                    'description': category.description or '',
                    'example': category.example or '',
                    'metadata': category.metadata,
                    'is_default': False  # Es personalizada
                }
            )

    # 3. RESUMEN FINAL
    total_user_expense_cats = UserExpenseCategory.objects.count()
    total_user_income_cats = UserIncomeCategory.objects.count()

    print(f"\n🎉 MIGRACIÓN COMPLETADA:")
    print(f"  - UserExpenseCategory creadas: {total_user_expense_cats}")
    print(f"  - UserIncomeCategory creadas: {total_user_income_cats}")


def reverse_migrate_categories(apps, schema_editor):
    """
    Revierte la migración eliminando todas las categorías de usuario
    """
    UserExpenseCategory = apps.get_model('categories', 'UserExpenseCategory')
    UserIncomeCategory = apps.get_model('categories', 'UserIncomeCategory')

    print("🔄 Revirtiendo migración de categorías...")

    user_expense_count = UserExpenseCategory.objects.count()
    user_income_count = UserIncomeCategory.objects.count()

    UserExpenseCategory.objects.all().delete()
    UserIncomeCategory.objects.all().delete()

    print(f"🗑️ Eliminadas {user_expense_count} UserExpenseCategory")
    print(f"🗑️ Eliminadas {user_income_count} UserIncomeCategory")


class Migration(migrations.Migration):

    dependencies = [
        ('categories', '0008_add_user_categories'),
        # Asegurar que expenses esté migrado
        ('expenses', '0009_expense_expense_embedding_idx'),
        # Asegurar que income esté migrado
        ('income', '0005_populate_income_categories'),
        ('users', '0013_remove_message_content_type_remove_message_media_url_and_more'),
    ]

    operations = [
        migrations.RunPython(
            migrate_categories_to_users,
            reverse_migrate_categories,
        ),
    ]
