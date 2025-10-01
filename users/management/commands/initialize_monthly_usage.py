from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from users.models import User
from users.models import MonthlyUsage
from datetime import datetime, date
from django.db.models import Count


class Command(BaseCommand):
    help = 'Inicializa los registros de uso mensual para usuarios existentes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ejecuta el comando sin hacer cambios reales (solo muestra lo que haría)',
        )
        parser.add_argument(
            '--user-id',
            type=str,
            help='Actualizar solo un usuario específico por external_id',
        )
        parser.add_argument(
            '--year',
            type=int,
            help='Año específico para procesar (por defecto: año actual)',
        )
        parser.add_argument(
            '--month',
            type=int,
            help='Mes específico para procesar (1-12, por defecto: mes actual)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        user_id = options.get('user_id')
        now = timezone.now()
        target_year = options.get('year') or now.year
        target_month = options.get('month') or now.month

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '🔍 MODO DRY-RUN: No se realizarán cambios reales')
            )

        # Filtrar usuarios si se especifica un ID
        if user_id:
            users = User.objects.filter(external_id=user_id)
            if not users.exists():
                self.stdout.write(
                    self.style.ERROR(
                        f'❌ Usuario con ID {user_id} no encontrado')
                )
                return
        else:
            users = User.objects.all()

        total_users = users.count()
        self.stdout.write(
            f'📊 Procesando {total_users} usuarios para {target_year}-{target_month:02d}...')

        created_count = 0
        updated_count = 0
        errors_count = 0

        with transaction.atomic():
            for user in users:
                try:
                    # Asegurar que el usuario tenga un plan básico por defecto
                    if not user.subscription_plan:
                        user.assign_basic_plan_if_none()

                    # Contar gastos e ingresos reales para el mes específico
                    start_date = date(target_year, target_month, 1)
                    if target_month == 12:
                        end_date = date(target_year + 1, 1, 1)
                    else:
                        end_date = date(target_year, target_month + 1, 1)

                    actual_expenses = user.expenses.filter(
                        created_at__gte=start_date,
                        created_at__lt=end_date
                    ).count()

                    actual_incomes = user.incomes.filter(
                        created_at__gte=start_date,
                        created_at__lt=end_date
                    ).count()

                    # Obtener o crear el registro de uso mensual
                    monthly_usage, created = MonthlyUsage.objects.get_or_create(
                        user=user,
                        year=target_year,
                        month=target_month,
                        defaults={
                            'expenses_count': actual_expenses,
                            'incomes_count': actual_incomes
                        }
                    )

                    if created:
                        created_count += 1
                        if not dry_run:
                            self.stdout.write(
                                f'✅ Creado uso mensual para {user.external_id} ({user.first_name or user.username}): '
                                f'Gastos: {actual_expenses}, Ingresos: {actual_incomes}'
                            )
                        else:
                            self.stdout.write(
                                f'🔍 Se crearía uso mensual para {user.external_id}: '
                                f'Gastos: {actual_expenses}, Ingresos: {actual_incomes}'
                            )
                    else:
                        # Verificar si necesita actualización
                        needs_update = (
                            monthly_usage.expenses_count != actual_expenses or
                            monthly_usage.incomes_count != actual_incomes
                        )

                        if needs_update:
                            old_expenses = monthly_usage.expenses_count
                            old_incomes = monthly_usage.incomes_count

                            if not dry_run:
                                monthly_usage.expenses_count = actual_expenses
                                monthly_usage.incomes_count = actual_incomes
                                monthly_usage.save()

                            updated_count += 1

                            self.stdout.write(
                                f'🔄 Usuario {user.external_id}: '
                                f'Gastos {old_expenses}→{actual_expenses}, '
                                f'Ingresos {old_incomes}→{actual_incomes}'
                            )
                        else:
                            self.stdout.write(
                                f'✓ Usuario {user.external_id}: Ya está actualizado '
                                f'(Gastos: {actual_expenses}, Ingresos: {actual_incomes})'
                            )

                except Exception as e:
                    errors_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'❌ Error procesando usuario {user.external_id}: {str(e)}'
                        )
                    )

        # Resumen final
        self.stdout.write('\n' + '='*50)
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'🔍 DRY-RUN COMPLETADO para {target_year}-{target_month:02d}:\n'
                    f'   - {created_count} registros mensuales se crearían\n'
                    f'   - {updated_count} registros se actualizarían\n'
                    f'   - {errors_count} errores encontrados'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'✅ PROCESO COMPLETADO para {target_year}-{target_month:02d}:\n'
                    f'   - {created_count} registros mensuales creados\n'
                    f'   - {updated_count} registros actualizados\n'
                    f'   - {errors_count} errores'
                )
            )

        if errors_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    f'⚠️  Se encontraron {errors_count} errores. '
                    'Revisa los logs para más detalles.'
                )
            )
