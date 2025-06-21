from django.core.management.base import BaseCommand
from django.db import transaction
from users.models import User
from categories.models import UserExpenseCategory, UserIncomeCategory


class Command(BaseCommand):
    help = 'Asigna categorías predefinidas a todos los usuarios existentes que no las tienen'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ejecutar en modo de prueba sin hacer cambios reales',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            help='Asignar categorías solo a un usuario específico por ID',
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

        # Obtener categorías predefinidas de los modelos
        predefined_expense_categories = UserExpenseCategory.PREDEFINED_CATEGORIES
        predefined_income_categories = UserIncomeCategory.PREDEFINED_CATEGORIES

        self.stdout.write(
            f'📊 Categorías predefinidas encontradas: {len(predefined_expense_categories)} gastos, {len(predefined_income_categories)} ingresos'
        )

        total_users = users.count()
        processed_users = 0
        total_expense_created = 0
        total_income_created = 0

        with transaction.atomic():
            for user in users:
                processed_users += 1

                self.stdout.write(
                    f'👤 Procesando usuario {processed_users}/{total_users}: {user.username} (ID: {user.id})'
                )

                # Verificar categorías de gastos existentes
                existing_expense_cats = UserExpenseCategory.objects.filter(
                    user=user, is_default=True
                ).values_list('name', flat=True)

                expense_created_count = 0
                for cat_name in predefined_expense_categories:
                    if cat_name not in existing_expense_cats:
                        if not dry_run:
                            UserExpenseCategory.objects.create(
                                user=user,
                                name=cat_name,
                                description=UserExpenseCategory.get_default_description(
                                    cat_name),
                                examples=UserExpenseCategory.get_default_examples(
                                    cat_name),
                                color=UserExpenseCategory.get_default_color(
                                    cat_name),
                                is_default=True
                            )
                        expense_created_count += 1
                        total_expense_created += 1

                # Verificar categorías de ingresos existentes
                existing_income_cats = UserIncomeCategory.objects.filter(
                    user=user, is_default=True
                ).values_list('name', flat=True)

                income_created_count = 0
                for cat_name in predefined_income_categories:
                    if cat_name not in existing_income_cats:
                        if not dry_run:
                            UserIncomeCategory.objects.create(
                                user=user,
                                name=cat_name,
                                description=UserIncomeCategory.get_default_description(
                                    cat_name),
                                example=UserIncomeCategory.get_default_example(
                                    cat_name),
                                color=UserIncomeCategory.get_default_color(
                                    cat_name),
                                is_default=True
                            )
                        income_created_count += 1
                        total_income_created += 1

                if expense_created_count > 0 or income_created_count > 0:
                    self.stdout.write(
                        f'   ✅ Creadas: {expense_created_count} gastos, {income_created_count} ingresos'
                    )
                else:
                    self.stdout.write(
                        '   ⏭️  Usuario ya tiene todas las categorías')

        # Resumen final
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('🎉 RESUMEN FINAL:'))
        self.stdout.write(f'👥 Usuarios procesados: {processed_users}')
        self.stdout.write(
            f'💰 Total categorías de gastos creadas: {total_expense_created}')
        self.stdout.write(
            f'📈 Total categorías de ingresos creadas: {total_income_created}')
        self.stdout.write(
            f'📊 Total categorías creadas: {total_expense_created + total_income_created}')

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '🧪 Esto fue una simulación. Ejecuta sin --dry-run para aplicar cambios.')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('✅ Cambios aplicados exitosamente.')
            )
