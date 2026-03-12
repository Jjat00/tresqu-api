import logging

from django.core.management.base import BaseCommand

from gmailbot.email_processor import process_history_update
from gmailbot.models import GoogleAccount

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Ejecuta una sincronización manual de Gmail para uno o todos los usuarios'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user_id',
            type=int,
            help='ID del usuario para sincronizar (users.User.id)',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Sincronizar todas las cuentas activas',
        )

    def handle(self, *args, **options):
        user_id = options.get('user_id')
        sync_all = options.get('all')

        if not user_id and not sync_all:
            self.stdout.write(
                self.style.ERROR(
                    'Debes especificar --user_id o --all'
                )
            )
            return

        if user_id:
            accounts = GoogleAccount.objects.filter(
                user_id=user_id,
                is_active=True,
            ).select_related('user')
        else:
            accounts = GoogleAccount.objects.filter(
                is_active=True,
            ).select_related('user')

        total = accounts.count()
        self.stdout.write(f"Sincronizando {total} cuenta(s) de Gmail")

        synced = 0
        errors = 0

        for google_account in accounts:
            try:
                # Verificar que existe un watch con history_id
                try:
                    watch = google_account.watch
                except Exception:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Sin watch para {google_account.google_email}, omitiendo"
                        )
                    )
                    continue

                if not watch.history_id:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Sin history_id para {google_account.google_email}, omitiendo"
                        )
                    )
                    continue

                self.stdout.write(f"Sincronizando {google_account.google_email}...")
                process_history_update(google_account, watch.history_id)
                synced += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Sincronización completada para {google_account.google_email}"
                    )
                )

            except Exception as e:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"Error sincronizando {google_account.google_email}: {e}"
                    )
                )
                logger.error(f"Error en sincronización manual de Gmail: {e}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Proceso completado: {synced} sincronizados, {errors} errores de {total} total"
            )
        )
