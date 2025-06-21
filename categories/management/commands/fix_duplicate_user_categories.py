from django.core.management.base import BaseCommand
from django.db import transaction
from users.models import User
from categories.models import UserExpenseCategory, UserIncomeCategory
from collections import defaultdict


class Command(BaseCommand):
    help = 'Elimina categorías duplicadas por usuario considerando mayúsculas/minúsculas'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ejecutar en modo de prueba sin hacer cambios reales',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            help='Limpiar categorías solo para un usuario específico por ID',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        user_id = options.get('user_id')

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '🧪 MODO DE PRUEBA - No se harán cambios reales')
            )

        # Filtrar usuarios
        if user_id:
            users = User.objects.filter(id=user_id)
            if not users.exists():
                self.stdout.write(
                    self.style.ERROR(
                        f'❌ Usuario con ID {user_id} no encontrado')
                )
                return
        else:
            users = User.objects.all()

        total_users = users.count()
        processed_users = 0
        total_expense_duplicates_removed = 0
        total_income_duplicates_removed = 0

        with transaction.atomic():
            for user in users:
                processed_users += 1

                self.stdout.write(
                    f'👤 Procesando usuario {processed_users}/{total_users}: {user.username} (ID: {user.id})'
                )

                # Procesar categorías de gastos
                expense_duplicates = self._find_duplicate_expense_categories(
                    user)
                expense_removed = self._remove_duplicate_expense_categories(
                    user, expense_duplicates, dry_run)
                total_expense_duplicates_removed += expense_removed

                # Procesar categorías de ingresos
                income_duplicates = self._find_duplicate_income_categories(
                    user)
                income_removed = self._remove_duplicate_income_categories(
                    user, income_duplicates, dry_run)
                total_income_duplicates_removed += income_removed

                if expense_removed > 0 or income_removed > 0:
                    self.stdout.write(
                        f'   🗑️  Eliminadas: {expense_removed} gastos, {income_removed} ingresos'
                    )
                else:
                    self.stdout.write(
                        '   ✅ No se encontraron duplicados')

        # Resumen final
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('🎉 RESUMEN FINAL:'))
        self.stdout.write(f'👥 Usuarios procesados: {processed_users}')
        self.stdout.write(
            f'💰 Categorías de gastos duplicadas eliminadas: {total_expense_duplicates_removed}')
        self.stdout.write(
            f'📈 Categorías de ingresos duplicadas eliminadas: {total_income_duplicates_removed}')
        self.stdout.write(
            f'📊 Total duplicados eliminados: {total_expense_duplicates_removed + total_income_duplicates_removed}')

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '🧪 Esto fue una simulación. Ejecuta sin --dry-run para aplicar cambios.')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('✅ Cambios aplicados exitosamente.')
            )

    def _find_duplicate_expense_categories(self, user):
        """Encuentra categorías de gastos duplicadas por usuario (case-insensitive)"""
        categories = UserExpenseCategory.objects.filter(user=user)

        # Agrupar por nombre en minúsculas para encontrar duplicados
        groups = defaultdict(list)
        for category in categories:
            normalized_name = category.name.lower().strip()
            groups[normalized_name].append(category)

        # Filtrar solo grupos con más de una categoría
        duplicates = {name: cats for name,
                      cats in groups.items() if len(cats) > 1}

        if duplicates:
            self.stdout.write(
                f'   🔍 Encontrados {len(duplicates)} grupos de categorías de gastos duplicadas:')
            for name, cats in duplicates.items():
                cat_names = [f"'{cat.name}' (ID: {cat.id})" for cat in cats]
                self.stdout.write(f'     - {name}: {", ".join(cat_names)}')

        return duplicates

    def _find_duplicate_income_categories(self, user):
        """Encuentra categorías de ingresos duplicadas por usuario (case-insensitive)"""
        categories = UserIncomeCategory.objects.filter(user=user)

        # Agrupar por nombre en minúsculas para encontrar duplicados
        groups = defaultdict(list)
        for category in categories:
            normalized_name = category.name.lower().strip()
            groups[normalized_name].append(category)

        # Filtrar solo grupos con más de una categoría
        duplicates = {name: cats for name,
                      cats in groups.items() if len(cats) > 1}

        if duplicates:
            self.stdout.write(
                f'   🔍 Encontrados {len(duplicates)} grupos de categorías de ingresos duplicadas:')
            for name, cats in duplicates.items():
                cat_names = [f"'{cat.name}' (ID: {cat.id})" for cat in cats]
                self.stdout.write(f'     - {name}: {", ".join(cat_names)}')

        return duplicates

    def _remove_duplicate_expense_categories(self, user, duplicates, dry_run):
        """Elimina categorías de gastos duplicadas, manteniendo la mejor opción"""
        removed_count = 0

        for normalized_name, categories in duplicates.items():
            # Ordenar por prioridad:
            # 1. Categorías con is_default=True
            # 2. Categorías con descripción y ejemplos
            # 3. Categorías más antiguas
            def priority_key(cat):
                has_default = 1 if cat.is_default else 0
                has_description = 1 if cat.description else 0
                has_examples = 1 if cat.examples else 0
                # Usar negativo para que las más antiguas tengan prioridad
                age_priority = -cat.id
                return (has_default, has_description, has_examples, age_priority)

            sorted_categories = sorted(
                categories, key=priority_key, reverse=True)

            # Mantener la primera (mejor) categoría
            keep_category = sorted_categories[0]
            to_remove = sorted_categories[1:]

            self.stdout.write(
                f'     ✅ Manteniendo: "{keep_category.name}" (ID: {keep_category.id})')

            for category in to_remove:
                self.stdout.write(
                    f'     🗑️  Eliminando: "{category.name}" (ID: {category.id})')
                if not dry_run:
                    # TODO: Actualizar referencias en gastos antes de eliminar
                    self._update_expense_references(category, keep_category)
                    category.delete()
                removed_count += 1

        return removed_count

    def _remove_duplicate_income_categories(self, user, duplicates, dry_run):
        """Elimina categorías de ingresos duplicadas, manteniendo la mejor opción"""
        removed_count = 0

        for normalized_name, categories in duplicates.items():
            # Ordenar por prioridad:
            # 1. Categorías con is_default=True
            # 2. Categorías con descripción y ejemplos
            # 3. Categorías más antiguas
            def priority_key(cat):
                has_default = 1 if cat.is_default else 0
                has_description = 1 if cat.description else 0
                has_example = 1 if cat.example else 0
                # Usar negativo para que las más antiguas tengan prioridad
                age_priority = -cat.id
                return (has_default, has_description, has_example, age_priority)

            sorted_categories = sorted(
                categories, key=priority_key, reverse=True)

            # Mantener la primera (mejor) categoría
            keep_category = sorted_categories[0]
            to_remove = sorted_categories[1:]

            self.stdout.write(
                f'     ✅ Manteniendo: "{keep_category.name}" (ID: {keep_category.id})')

            for category in to_remove:
                self.stdout.write(
                    f'     🗑️  Eliminando: "{category.name}" (ID: {category.id})')
                if not dry_run:
                    # TODO: Actualizar referencias en ingresos antes de eliminar
                    self._update_income_references(category, keep_category)
                    category.delete()
                removed_count += 1

        return removed_count

    def _update_expense_references(self, old_category, new_category):
        """Actualiza las referencias en los gastos de la categoría antigua a la nueva"""
        from expenses.models import Expense

        expenses_to_update = Expense.objects.filter(
            user_expense_category=old_category)
        count = expenses_to_update.count()

        if count > 0:
            expenses_to_update.update(user_expense_category=new_category)
            self.stdout.write(
                f'       📝 Actualizados {count} gastos para usar la nueva categoría')

    def _update_income_references(self, old_category, new_category):
        """Actualiza las referencias en los ingresos de la categoría antigua a la nueva"""
        from income.models import Income

        incomes_to_update = Income.objects.filter(
            user_income_category=old_category)
        count = incomes_to_update.count()

        if count > 0:
            incomes_to_update.update(user_income_category=new_category)
            self.stdout.write(
                f'       📝 Actualizados {count} ingresos para usar la nueva categoría')
