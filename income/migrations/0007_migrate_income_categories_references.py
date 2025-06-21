# Generated manually for income categories migration
from django.db import migrations


def migrate_income_category_references(apps, schema_editor):
    """
    Migra las referencias de categorías en ingresos existentes:
    Para cada ingreso existente, encuentra la UserIncomeCategory correspondiente del usuario
    y la asigna al campo user_income_category
    """
    Income = apps.get_model('income', 'Income')
    UserIncomeCategory = apps.get_model('categories', 'UserIncomeCategory')

    print("🚀 Iniciando migración de referencias de categorías en ingresos...")

    all_incomes = Income.objects.all()
    print(f"📊 Ingresos encontrados: {all_incomes.count()}")

    updated_count = 0
    errors_count = 0

    for income in all_incomes:
        try:
            # Obtener el nombre de la categoría del ingreso
            if income.category:
                category_name = income.category.name
            elif income.category_str:
                category_name = income.category_str
            else:
                print(f"⚠️  Ingreso ID {income.id}: Sin categoría definida")
                continue

            # Buscar la UserIncomeCategory correspondiente para este usuario
            try:
                user_income_category = UserIncomeCategory.objects.get(
                    user=income.user,
                    name=category_name
                )

                # Asignar la categoría de usuario al ingreso
                income.user_income_category = user_income_category
                income.save()

                updated_count += 1
                print(
                    f"✅ Ingreso ID {income.id}: '{category_name}' → UserIncomeCategory ID {user_income_category.id}")

            except UserIncomeCategory.DoesNotExist:
                # La categoría no existe para este usuario, crear una nueva
                print(
                    f"🔄 Creando categoría '{category_name}' para usuario {income.user.id}...")

                # Obtener datos de la categoría original si existe
                color = '#CCCCCC'
                description = ''
                example = ''
                metadata = None

                if income.category:
                    color = income.category.color
                    description = income.category.description or ''
                    example = income.category.example or ''
                    metadata = income.category.metadata

                # Crear la nueva UserIncomeCategory
                user_income_category = UserIncomeCategory.objects.create(
                    user=income.user,
                    name=category_name,
                    color=color,
                    description=description,
                    example=example,
                    metadata=metadata,
                    is_default=False  # Es personalizada porque no existía para este usuario
                )

                # Asignar al ingreso
                income.user_income_category = user_income_category
                income.save()

                updated_count += 1
                print(
                    f"✅ Ingreso ID {income.id}: Creada y asignada '{category_name}' → UserIncomeCategory ID {user_income_category.id}")

        except Exception as e:
            errors_count += 1
            print(f"❌ Error procesando ingreso ID {income.id}: {str(e)}")

    print(f"\n🎉 MIGRACIÓN DE INGRESOS COMPLETADA:")
    print(f"  - Ingresos actualizados: {updated_count}")
    print(f"  - Errores: {errors_count}")


def reverse_income_category_references(apps, schema_editor):
    """
    Revierte la migración limpiando las referencias user_income_category
    """
    Income = apps.get_model('income', 'Income')

    print("🔄 Revirtiendo migración de referencias de categorías en ingresos...")

    updated_count = Income.objects.filter(
        user_income_category__isnull=False).count()
    Income.objects.all().update(user_income_category=None)

    print(f"🗑️ Limpiadas {updated_count} referencias user_income_category")


class Migration(migrations.Migration):

    dependencies = [
        ('income', '0006_add_user_income_category'),
        ('categories', '0009_migrate_user_categories_data'),
    ]

    operations = [
        migrations.RunPython(
            migrate_income_category_references,
            reverse_income_category_references,
        ),
    ]
