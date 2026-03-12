import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from gmailbot.gmail_service import setup_watch
from gmailbot.models import GmailWatch

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Renueva los watches de Gmail que expiran en menos de 24 horas'

    def handle(self, *args, **options):
        threshold = timezone.now() + timedelta(hours=24)

        # Buscar watches activos que expiran pronto
        expiring_watches = GmailWatch.objects.filter(
            is_active=True,
            watch_expiration__lte=threshold,
            google_account__is_active=True,
        ).select_related('google_account')

        total = expiring_watches.count()
        self.stdout.write(f"Encontrados {total} watches por renovar")

        renewed = 0
        errors = 0

        for watch in expiring_watches:
            try:
                google_account = watch.google_account
                setup_watch(google_account)
                renewed += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Watch renovado para {google_account.google_email}"
                    )
                )
            except Exception as e:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"Error renovando watch para {watch.google_account.google_email}: {e}"
                    )
                )
                logger.error(f"Error renovando watch: {e}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Proceso completado: {renewed} renovados, {errors} errores de {total} total"
            )
        )
