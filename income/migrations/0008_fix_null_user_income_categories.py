from django.db import migrations
from django.db import transaction


def fix_null_user_income_categories(apps, schema_editor):
    """
    Migración para corregir ingresos que tienen user_income_category_id NULL.

    Esto puede ocurrir cuando:
    1. Se crearon ingresos después de agregar el campo pero antes de actualizar el código
    2. Hubo problemas en la migración anterior
    3. Se crearon ingresos manualmente sin asignar la user_income_category
    """
    Income = apps.get_model('income', 'Income')
    IncomeCategory = apps.get_model('income', 'IncomeCategory')
    UserIncomeCategory = apps.get_model('categories', 'UserIncomeCategory')
    User = apps.get_model('users', 'User')

    print("🔧 Iniciando corrección de ingresos con user_income_category_id NULL...")

    # Encontrar ingresos con user_income_category_id NULL
    ingresos_null = Income.objects.filter(user_income_category_id__isnull=True)
    total_ingresos_null = ingresos_null.count()

    print(
        f"📊 Encontrados {total_ingresos_null} ingresos con user_income_category_id NULL")

    if total_ingresos_null == 0:
        print("✅ No hay ingresos con user_income_category_id NULL. Migración completada.")
        return

    ingresos_corregidos = 0
    ingresos_con_error = 0

    with transaction.atomic():
        for ingreso in ingresos_null:
            try:
                # Verificar que el ingreso tenga usuario
                if not ingreso.user_id:
                    print(
                        f"⚠️  Ingreso ID {ingreso.id} no tiene usuario asignado. Saltando...")
                    ingresos_con_error += 1
                    continue

                # Caso 1: El ingreso tiene category_id (categoría global)
                if ingreso.category_id:
                    categoria_global = IncomeCategory.objects.get(
                        id=ingreso.category_id)

                    # Buscar o crear UserIncomeCategory equivalente
                    user_category, created = UserIncomeCategory.objects.get_or_create(
                        user_id=ingreso.user_id,
                        name=categoria_global.name,
                        defaults={
                            'description': categoria_global.description or '',
                            'example': categoria_global.example or '',
                            'is_default': True,  # Las categorías globales se consideran predefinidas
                        }
                    )

                    # Asignar la user_income_category al ingreso
                    ingreso.user_income_category_id = user_category.id
                    ingreso.save(update_fields=['user_income_category_id'])

                    action = "creada" if created else "encontrada"
                    print(
                        f"✅ Ingreso ID {ingreso.id}: UserIncomeCategory '{user_category.name}' {action} y asignada")
                    ingresos_corregidos += 1

                # Caso 2: El ingreso NO tiene category_id (categoría global)
                else:
                    # Buscar una categoría por defecto para el usuario o crear "Otros Ingresos"
                    user_category, created = UserIncomeCategory.objects.get_or_create(
                        user_id=ingreso.user_id,
                        name="Otros Ingresos",
                        defaults={
                            'description': 'Ingresos sin categoría específica',
                            'example': 'Ingresos diversos, varios',
                            'is_default': False,
                        }
                    )

                    # Asignar la user_income_category al ingreso
                    ingreso.user_income_category_id = user_category.id
                    ingreso.save(update_fields=['user_income_category_id'])

                    action = "creada" if created else "encontrada"
                    print(
                        f"✅ Ingreso ID {ingreso.id}: UserIncomeCategory 'Otros Ingresos' {action} y asignada (sin categoría original)")
                    ingresos_corregidos += 1

            except Exception as e:
                print(f"❌ Error procesando ingreso ID {ingreso.id}: {str(e)}")
                ingresos_con_error += 1
                continue

    print(f"\n📊 RESUMEN DE CORRECCIÓN:")
    print(f"  - Total ingresos con NULL: {total_ingresos_null}")
    print(f"  - Ingresos corregidos: {ingresos_corregidos}")
    print(f"  - Ingresos con error: {ingresos_con_error}")
    print(
        f"  - Éxito: {(ingresos_corregidos/total_ingresos_null*100) if total_ingresos_null > 0 else 100:.1f}%")


def reverse_fix_null_user_income_categories(apps, schema_editor):
    """
    Función de reversión (no implementada ya que no queremos deshacer la corrección)
    """
    print("⚠️  La reversión de esta migración no está implementada para evitar pérdida de datos.")
    print("   Si necesitas revertir, hazlo manualmente.")


class Migration(migrations.Migration):
    dependencies = [
        ('income', '0007_migrate_income_categories_references'),
        ('categories', '0009_migrate_user_categories_data'),
    ]

    operations = [
        migrations.RunPython(
            fix_null_user_income_categories,
            reverse_fix_null_user_income_categories,
        ),
    ]
