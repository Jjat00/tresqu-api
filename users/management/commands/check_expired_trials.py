from django.core.management.base import BaseCommand
from django.utils import timezone
from users.models import User


class Command(BaseCommand):
    help = 'Verifica y degrada a usuarios con suscripciones de prueba expiradas al plan básico'

    def handle(self, *args, **options):
        # Obtener fecha actual
        now = timezone.now()

        # Buscar usuarios con plan premium en periodo de prueba expirado
        expired_users = User.objects.filter(
            subscription_active=True,
            subscription_end_date__lt=now,
        )

        count = 0
        for user in expired_users:
            if user.is_trial_expired and user.is_premium:
                user.downgrade_to_basic()
                count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Usuario {user} degradado al plan básico'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Proceso completado. {count} usuarios degradados al plan básico.'
            )
        )
