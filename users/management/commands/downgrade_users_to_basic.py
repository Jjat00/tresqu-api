from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from users.models import User, SubscriptionPlan
from datetime import datetime


class Command(BaseCommand):
    help = 'Migra usuarios de Premium/Business a plan Basic'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ejecuta el comando sin hacer cambios reales (solo muestra lo que haría)',
        )
        parser.add_argument(
            '--user-id',
            type=str,
            help='Migrar solo un usuario específico por external_id',
        )
        parser.add_argument(
            '--from-plan',
            type=str,
            choices=['PREMIUM', 'BUSINESS', 'ALL'],
            default='ALL',
            help='Migrar solo usuarios de un plan específico (por defecto: ALL)',
        )
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirmar la migración masiva (requerido para migrar múltiples usuarios)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        user_id = options.get('user_id')
        from_plan = options.get('from_plan', 'ALL')
        confirm = options['confirm']

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '🔍 MODO DRY-RUN: No se realizarán cambios reales')
            )

        # Construir filtro de usuarios
        user_filter = {}

        if user_id:
            user_filter['external_id'] = user_id

        if from_plan != 'ALL':
            user_filter['subscription_plan__name'] = from_plan
        else:
            user_filter['subscription_plan__name__in'] = [
                'PREMIUM', 'BUSINESS']

        # Obtener usuarios a migrar
        users_to_migrate = User.objects.filter(
            **user_filter).select_related('subscription_plan')

        if not users_to_migrate.exists():
            self.stdout.write(
                self.style.WARNING(
                    '❌ No se encontraron usuarios para migrar con los criterios especificados')
            )
            return

        total_users = users_to_migrate.count()

        # Mostrar resumen
        self.stdout.write(f'📊 Usuarios encontrados para migrar: {total_users}')

        # Agrupar por plan actual
        plan_counts = {}
        for user in users_to_migrate:
            plan_name = user.subscription_plan.name if user.subscription_plan else 'SIN_PLAN'
            plan_counts[plan_name] = plan_counts.get(plan_name, 0) + 1

        for plan, count in plan_counts.items():
            self.stdout.write(f'   - {plan}: {count} usuarios')

        # Verificar confirmación para migración masiva
        if not user_id and total_users > 1 and not confirm and not dry_run:
            self.stdout.write(
                self.style.ERROR(
                    f'⚠️  Estás a punto de migrar {total_users} usuarios. '
                    'Usa --confirm para confirmar la migración masiva.'
                )
            )
            return

        # Procesar migración
        migrated_count = 0
        errors_count = 0

        with transaction.atomic():
            for user in users_to_migrate:
                try:
                    old_plan = user.subscription_plan.name if user.subscription_plan else 'SIN_PLAN'

                    if not dry_run:
                        # Ejecutar migración real
                        user.downgrade_to_basic()
                        migrated_count += 1

                        self.stdout.write(
                            f'✅ Usuario {user.external_id} ({user.first_name or user.username}): '
                            f'{old_plan} → BASIC'
                        )
                    else:
                        # Solo mostrar lo que se haría
                        migrated_count += 1
                        self.stdout.write(
                            f'🔍 Se migraría: {user.external_id} ({user.first_name or user.username}): '
                            f'{old_plan} → BASIC'
                        )

                except Exception as e:
                    errors_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'❌ Error migrando usuario {user.external_id}: {str(e)}'
                        )
                    )

        # Resumen final
        self.stdout.write('\n' + '='*50)
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f'🔍 DRY-RUN COMPLETADO:\n'
                    f'   - {migrated_count} usuarios se migrarían a BASIC\n'
                    f'   - {errors_count} errores encontrados'
                )
            )
            if migrated_count > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f'\n💡 Para ejecutar la migración real, ejecuta el mismo comando sin --dry-run'
                    )
                )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'✅ MIGRACIÓN COMPLETADA:\n'
                    f'   - {migrated_count} usuarios migrados a BASIC\n'
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

        # Mostrar estadísticas finales
        if not dry_run and migrated_count > 0:
            basic_plan = SubscriptionPlan.get_basic_plan()
            total_basic_users = User.objects.filter(
                subscription_plan=basic_plan).count()
            total_premium_users = User.objects.filter(
                subscription_plan__name__in=['PREMIUM', 'BUSINESS']
            ).count()

            self.stdout.write(
                f'\n📈 ESTADÍSTICAS ACTUALES:\n'
                f'   - Usuarios BASIC: {total_basic_users}\n'
                f'   - Usuarios PREMIUM/BUSINESS: {total_premium_users}'
            )
