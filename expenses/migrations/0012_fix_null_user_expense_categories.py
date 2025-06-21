from django.db import migrations
from django.db import transaction


def fix_null_user_expense_categories(apps, schema_editor):
    """
    Migración para corregir gastos que tienen user_expense_category_id NULL.

    Esto puede ocurrir cuando:
    1. Se crearon gastos después de agregar el campo pero antes de actualizar el código
    2. Hubo problemas en la migración anterior
    3. Se crearon gastos manualmente sin asignar la user_expense_category
    """
    Expense = apps.get_model('expenses', 'Expense')
    Category = apps.get_model('categories', 'Category')
    UserExpenseCategory = apps.get_model('categories', 'UserExpenseCategory')
    User = apps.get_model('users', 'User')

    print("🔧 Iniciando corrección de gastos con user_expense_category_id NULL...")

    # Encontrar gastos con user_expense_category_id NULL
    gastos_null = Expense.objects.filter(user_expense_category_id__isnull=True)
    total_gastos_null = gastos_null.count()

    print(
        f"📊 Encontrados {total_gastos_null} gastos con user_expense_category_id NULL")

    if total_gastos_null == 0:
        print("✅ No hay gastos con user_expense_category_id NULL. Migración completada.")
        return

    gastos_corregidos = 0
    gastos_con_error = 0

    with transaction.atomic():
        for gasto in gastos_null:
            try:
                # Verificar que el gasto tenga usuario
                if not gasto.user_id:
                    print(
                        f"⚠️  Gasto ID {gasto.id} no tiene usuario asignado. Saltando...")
                    gastos_con_error += 1
                    continue

                # Caso 1: El gasto tiene category_id (categoría global)
                if gasto.category_id:
                    categoria_global = Category.objects.get(
                        id=gasto.category_id)

                    # Buscar o crear UserExpenseCategory equivalente
                    user_category, created = UserExpenseCategory.objects.get_or_create(
                        user_id=gasto.user_id,
                        name=categoria_global.name,
                        defaults={
                            'description': categoria_global.description or '',
                            'examples': categoria_global.examples or '',
                            'color': categoria_global.color or '#6B7280',
                            'is_default': True,  # Las categorías globales se consideran predefinidas
                        }
                    )

                    # Asignar la user_expense_category al gasto
                    gasto.user_expense_category_id = user_category.id
                    gasto.save(update_fields=['user_expense_category_id'])

                    action = "creada" if created else "encontrada"
                    print(
                        f"✅ Gasto ID {gasto.id}: UserExpenseCategory '{user_category.name}' {action} y asignada")
                    gastos_corregidos += 1

                # Caso 2: El gasto NO tiene category_id (categoría global)
                else:
                    # Buscar una categoría por defecto para el usuario o crear "Otros"
                    user_category, created = UserExpenseCategory.objects.get_or_create(
                        user_id=gasto.user_id,
                        name="Otros",
                        defaults={
                            'description': 'Gastos sin categoría específica',
                            'examples': 'Gastos diversos, varios',
                            'color': '#6B7280',
                            'is_default': False,
                        }
                    )

                    # Asignar la user_expense_category al gasto
                    gasto.user_expense_category_id = user_category.id
                    gasto.save(update_fields=['user_expense_category_id'])

                    action = "creada" if created else "encontrada"
                    print(
                        f"✅ Gasto ID {gasto.id}: UserExpenseCategory 'Otros' {action} y asignada (sin categoría original)")
                    gastos_corregidos += 1

            except Exception as e:
                print(f"❌ Error procesando gasto ID {gasto.id}: {str(e)}")
                gastos_con_error += 1
                continue

    print(f"\n📊 RESUMEN DE CORRECCIÓN:")
    print(f"  - Total gastos con NULL: {total_gastos_null}")
    print(f"  - Gastos corregidos: {gastos_corregidos}")
    print(f"  - Gastos con error: {gastos_con_error}")
    print(
        f"  - Éxito: {(gastos_corregidos/total_gastos_null*100) if total_gastos_null > 0 else 100:.1f}%")


def reverse_fix_null_user_expense_categories(apps, schema_editor):
    """
    Función de reversión (no implementada ya que no queremos deshacer la corrección)
    """
    print("⚠️  La reversión de esta migración no está implementada para evitar pérdida de datos.")
    print("   Si necesitas revertir, hazlo manualmente.")


class Migration(migrations.Migration):
    dependencies = [
        ('expenses', '0011_migrate_expense_categories_references'),
        ('categories', '0009_migrate_user_categories_data'),
    ]

    operations = [
        migrations.RunPython(
            fix_null_user_expense_categories,
            reverse_fix_null_user_expense_categories,
        ),
    ]
