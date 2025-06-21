from django.core.management.base import BaseCommand
from django.db import transaction
from django.db import models
from users.models import User
from categories.models import UserExpenseCategory, UserIncomeCategory


class Command(BaseCommand):
    help = 'Corrige categorías que no tienen descripción ni ejemplos, asignando valores predefinidos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ejecutar en modo de prueba sin hacer cambios reales',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            help='Corregir categorías solo para un usuario específico por ID',
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
        total_expense_categories_fixed = 0
        total_income_categories_fixed = 0

        with transaction.atomic():
            for user in users:
                processed_users += 1

                self.stdout.write(
                    f'👤 Procesando usuario {processed_users}/{total_users}: {user.username} (ID: {user.id})'
                )

                # Procesar categorías de gastos
                expense_fixed = self._fix_empty_expense_categories(
                    user, dry_run)
                total_expense_categories_fixed += expense_fixed

                # Procesar categorías de ingresos
                income_fixed = self._fix_empty_income_categories(user, dry_run)
                total_income_categories_fixed += income_fixed

                if expense_fixed > 0 or income_fixed > 0:
                    self.stdout.write(
                        f'   🔧 Corregidas: {expense_fixed} gastos, {income_fixed} ingresos'
                    )
                else:
                    self.stdout.write(
                        '   ✅ Todas las categorías tienen descripción y ejemplos')

        # Resumen final
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('🎉 RESUMEN FINAL:'))
        self.stdout.write(f'👥 Usuarios procesados: {processed_users}')
        self.stdout.write(
            f'💰 Categorías de gastos corregidas: {total_expense_categories_fixed}')
        self.stdout.write(
            f'📈 Categorías de ingresos corregidas: {total_income_categories_fixed}')
        self.stdout.write(
            f'📊 Total categorías corregidas: {total_expense_categories_fixed + total_income_categories_fixed}')

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '🧪 Esto fue una simulación. Ejecuta sin --dry-run para aplicar cambios.')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('✅ Cambios aplicados exitosamente.')
            )

    def _fix_empty_expense_categories(self, user, dry_run):
        """Corrige categorías de gastos que no tienen descripción ni ejemplos"""
        # Buscar categorías sin descripción o sin ejemplos
        empty_categories = UserExpenseCategory.objects.filter(
            user=user
        ).filter(
            models.Q(description__isnull=True) |
            models.Q(description='') |
            models.Q(examples__isnull=True) |
            models.Q(examples='')
        )

        fixed_count = 0

        for category in empty_categories:
            # Obtener valores predefinidos
            default_description = UserExpenseCategory.get_default_description(
                category.name)
            default_examples = UserExpenseCategory.get_default_examples(
                category.name)

            updated = False
            changes = []

            if not category.description and default_description:
                category.description = default_description
                updated = True
                changes.append("descripción")

            if not category.examples and default_examples:
                category.examples = default_examples
                updated = True
                changes.append("ejemplos")

            if updated:
                self.stdout.write(
                    f'     🔧 Corrigiendo "{category.name}": {", ".join(changes)}')
                if not dry_run:
                    category.save()
                fixed_count += 1

        return fixed_count

    def _fix_empty_income_categories(self, user, dry_run):
        """Corrige categorías de ingresos que no tienen descripción ni ejemplos"""
        # Buscar categorías sin descripción o sin ejemplos
        empty_categories = UserIncomeCategory.objects.filter(
            user=user
        ).filter(
            models.Q(description__isnull=True) |
            models.Q(description='') |
            models.Q(example__isnull=True) |
            models.Q(example='')
        )

        fixed_count = 0

        for category in empty_categories:
            # Obtener valores predefinidos
            default_description = UserIncomeCategory.get_default_description(
                category.name)
            default_example = UserIncomeCategory.get_default_example(
                category.name)

            updated = False
            changes = []

            if not category.description and default_description:
                category.description = default_description
                updated = True
                changes.append("descripción")

            if not category.example and default_example:
                category.example = default_example
                updated = True
                changes.append("ejemplos")

            if updated:
                self.stdout.write(
                    f'     🔧 Corrigiendo "{category.name}": {", ".join(changes)}')
                if not dry_run:
                    category.save()
                fixed_count += 1

        return fixed_count
