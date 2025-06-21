# Generated manually for expense categories migration
from django.db import migrations


def migrate_expense_category_references(apps, schema_editor):
    """
    Migra las referencias de categorías en gastos existentes:
    Para cada gasto existente, encuentra la UserExpenseCategory correspondiente del usuario
    y la asigna al campo user_expense_category
    """
    Expense = apps.get_model('expenses', 'Expense')
    UserExpenseCategory = apps.get_model('categories', 'UserExpenseCategory')

    print("🚀 Iniciando migración de referencias de categorías en gastos...")

    all_expenses = Expense.objects.all()
    print(f"📊 Gastos encontrados: {all_expenses.count()}")

    updated_count = 0
    errors_count = 0

    for expense in all_expenses:
        try:
            # Obtener el nombre de la categoría del gasto
            if expense.category:
                category_name = expense.category.name
            elif expense.category_str:
                category_name = expense.category_str
            else:
                print(f"⚠️  Gasto ID {expense.id}: Sin categoría definida")
                continue

            # Buscar la UserExpenseCategory correspondiente para este usuario
            try:
                user_expense_category = UserExpenseCategory.objects.get(
                    user=expense.user,
                    name=category_name
                )

                # Asignar la categoría de usuario al gasto
                expense.user_expense_category = user_expense_category
                expense.save()

                updated_count += 1
                print(
                    f"✅ Gasto ID {expense.id}: '{category_name}' → UserExpenseCategory ID {user_expense_category.id}")

            except UserExpenseCategory.DoesNotExist:
                # La categoría no existe para este usuario, crear una nueva
                print(
                    f"🔄 Creando categoría '{category_name}' para usuario {expense.user.id}...")

                # Obtener datos de la categoría original si existe
                color = '#CCCCCC'
                description = ''
                examples = ''
                metadata = None

                if expense.category:
                    color = expense.category.color
                    description = expense.category.description or ''
                    examples = expense.category.examples or ''
                    metadata = expense.category.metadata

                # Crear la nueva UserExpenseCategory
                user_expense_category = UserExpenseCategory.objects.create(
                    user=expense.user,
                    name=category_name,
                    color=color,
                    description=description,
                    examples=examples,
                    metadata=metadata,
                    is_default=False  # Es personalizada porque no existía para este usuario
                )

                # Asignar al gasto
                expense.user_expense_category = user_expense_category
                expense.save()

                updated_count += 1
                print(
                    f"✅ Gasto ID {expense.id}: Creada y asignada '{category_name}' → UserExpenseCategory ID {user_expense_category.id}")

        except Exception as e:
            errors_count += 1
            print(f"❌ Error procesando gasto ID {expense.id}: {str(e)}")

    print(f"\n🎉 MIGRACIÓN DE GASTOS COMPLETADA:")
    print(f"  - Gastos actualizados: {updated_count}")
    print(f"  - Errores: {errors_count}")


def reverse_expense_category_references(apps, schema_editor):
    """
    Revierte la migración limpiando las referencias user_expense_category
    """
    Expense = apps.get_model('expenses', 'Expense')

    print("🔄 Revirtiendo migración de referencias de categorías en gastos...")

    updated_count = Expense.objects.filter(
        user_expense_category__isnull=False).count()
    Expense.objects.all().update(user_expense_category=None)

    print(f"🗑️ Limpiadas {updated_count} referencias user_expense_category")


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0010_add_user_expense_category'),
        ('categories', '0009_migrate_user_categories_data'),
    ]

    operations = [
        migrations.RunPython(
            migrate_expense_category_references,
            reverse_expense_category_references,
        ),
    ]
