"""
Comando para enviar recordatorios diarios automáticos
Uso: python manage.py send_daily_reminders
Se puede programar con cron para ejecutarse automáticamente
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from users.models import User, Chat, Message
from whatsappbot.views import send_meta_whatsapp_message, get_template_message
from datetime import datetime, timedelta
import time
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Envía recordatorios diarios a usuarios activos de WhatsApp'

    def add_arguments(self, parser):
        parser.add_argument(
            '--template',
            type=str,
            default='daily_summary',
            choices=['reminder', 'daily_summary', 'weekly_reminder'],
            help='Template de mensaje a usar',
        )
        parser.add_argument(
            '--active-days',
            type=int,
            default=7,
            help='Días de actividad para considerar usuario activo (por defecto 7)',
        )
        parser.add_argument(
            '--delay',
            type=int,
            default=3,
            help='Delay en segundos entre mensajes (por defecto 3 segundos)',
        )
        parser.add_argument(
            '--max-users',
            type=int,
            default=100,
            help='Máximo número de usuarios a procesar por ejecución',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar qué usuarios recibirían el mensaje sin enviarlo',
        )

    def handle(self, *args, **options):
        template = options['template']
        active_days = options['active_days']
        delay = options['delay']
        max_users = options['max_users']
        dry_run = options['dry_run']

        self.stdout.write(
            self.style.SUCCESS('📅 Iniciando envío de recordatorios diarios')
        )
        self.stdout.write('=' * 60)

        # Obtener usuarios activos
        cutoff_date = datetime.now() - timedelta(days=active_days)

        # Usuarios que han enviado mensajes recientemente
        active_chats = Chat.objects.filter(
            platform='WHATSAPP',
            messages__created_at__gte=cutoff_date
        ).distinct()

        active_users = User.objects.filter(
            platform__in=['WHATSAPP', 'MULTIPLTAFORMA'],
            phone_number__isnull=False,
            chats__in=active_chats
        ).exclude(phone_number='').distinct()[:max_users]

        total_users = active_users.count()

        if total_users == 0:
            self.stdout.write(
                self.style.WARNING(
                    f'⚠️ No se encontraron usuarios activos en los últimos {active_days} días')
            )
            return

        # Obtener mensaje del template
        message = get_template_message(template)

        if not message:
            self.stdout.write(
                self.style.ERROR(f'❌ Template "{template}" no válido')
            )
            return

        self.stdout.write(f"👥 Usuarios activos encontrados: {total_users}")
        self.stdout.write(f"📅 Actividad en los últimos {active_days} días")
        self.stdout.write(f"📝 Template: {template}")
        self.stdout.write(f"⏱️ Delay entre mensajes: {delay} segundos")
        self.stdout.write("-" * 60)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '🧪 MODO DRY-RUN - No se enviarán mensajes reales')
            )
            self.show_users_preview(active_users, message)
            return

        # Enviar recordatorios
        self.send_daily_reminders(active_users, message, delay)

    def show_users_preview(self, users, message):
        """Muestra una vista previa de los usuarios y el mensaje"""
        self.stdout.write("\n👥 Usuarios que recibirían el recordatorio:")
        self.stdout.write("-" * 40)

        for i, user in enumerate(users[:10], 1):
            last_activity = self.get_last_activity(user)
            self.stdout.write(
                f"{i}. 📱 {user.first_name} - {user.phone_number}"
            )
            self.stdout.write(f"   Última actividad: {last_activity}")

        if users.count() > 10:
            remaining = users.count() - 10
            self.stdout.write(f"... y {remaining} usuarios más")

        self.stdout.write(f"\n💬 Mensaje que se enviaría:")
        self.stdout.write("-" * 40)
        sample_message = message.replace("{name}", "Usuario Ejemplo")
        self.stdout.write(sample_message)

    def get_last_activity(self, user):
        """Obtiene la fecha de última actividad del usuario"""
        try:
            last_message = Message.objects.filter(
                chat__user=user,
                chat__platform='WHATSAPP',
                message_type='incoming'
            ).order_by('-created_at').first()

            if last_message:
                return last_message.created_at.strftime('%Y-%m-%d %H:%M')
            return "Sin actividad reciente"
        except:
            return "Desconocida"

    def send_daily_reminders(self, users, message, delay):
        """Envía los recordatorios diarios"""
        sent_count = 0
        failed_count = 0
        total_users = users.count()

        self.stdout.write(f"\n📤 Iniciando envío de recordatorios...")
        self.stdout.write("-" * 50)

        for i, user in enumerate(users, 1):
            try:
                # Personalizar mensaje con el nombre del usuario
                personalized_message = message.replace(
                    "{name}", user.first_name or "")

                # Enviar recordatorio usando Meta WhatsApp API
                success = send_meta_whatsapp_message(
                    phone_number=user.phone_number,
                    message_text=personalized_message
                )

                if success:
                    sent_count += 1
                    self.stdout.write(
                        f"✅ [{i}/{total_users}] {user.first_name} ({user.phone_number})"
                    )
                else:
                    failed_count += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"❌ [{i}/{total_users}] FALLÓ: {user.first_name} ({user.phone_number})"
                        )
                    )

                # Delay entre mensajes para evitar rate limiting
                if i < total_users:
                    time.sleep(delay)

            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"❌ [{i}/{total_users}] ERROR: {user.first_name} - {str(e)}"
                    )
                )
                continue

        # Resumen final
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(f"📊 RESUMEN DE RECORDATORIOS DIARIOS")
        )
        self.stdout.write(f"✅ Recordatorios enviados: {sent_count}")
        self.stdout.write(f"❌ Recordatorios fallidos: {failed_count}")
        self.stdout.write(f"👥 Total de usuarios procesados: {total_users}")

        success_rate = (sent_count / total_users *
                        100) if total_users > 0 else 0
        self.stdout.write(f"📈 Tasa de éxito: {success_rate:.1f}%")

        # Log para el sistema
        logger.info(
            f"Recordatorios diarios enviados: {sent_count}/{total_users} ({success_rate:.1f}%)")

        if failed_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"\n⚠️ {failed_count} recordatorios fallaron. Revisa los logs para más detalles."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "\n🎉 ¡Todos los recordatorios se enviaron exitosamente!")
            )
