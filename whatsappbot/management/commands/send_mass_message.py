"""
Comando de Django para enviar mensajes masivos a usuarios de WhatsApp
Uso: python manage.py send_mass_message --message "Tu mensaje aquí"
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from users.models import User
from whatsappbot.views import send_meta_whatsapp_message
import time
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Envía un mensaje masivo a todos los usuarios de WhatsApp registrados'

    def add_arguments(self, parser):
        parser.add_argument(
            '--message',
            type=str,
            required=True,
            help='Mensaje a enviar a todos los usuarios',
        )
        parser.add_argument(
            '--platform',
            type=str,
            default='WHATSAPP',
            choices=['WHATSAPP', 'ALL'],
            help='Plataforma de usuarios (WHATSAPP o ALL para todas)',
        )
        parser.add_argument(
            '--delay',
            type=int,
            default=2,
            help='Delay en segundos entre mensajes (por defecto 2 segundos)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Solo mostrar qué usuarios recibirían el mensaje sin enviarlo',
        )
        parser.add_argument(
            '--template',
            type=str,
            choices=['reminder', 'welcome', 'custom'],
            help='Usar un template predefinido',
        )

    def handle(self, *args, **options):
        message = options['message']
        platform = options['platform']
        delay = options['delay']
        dry_run = options['dry_run']
        template = options['template']

        # Usar template predefinido si se especifica
        if template:
            message = self.get_template_message(template)
            self.stdout.write(f"📝 Usando template '{template}': {message}")

        self.stdout.write(
            self.style.SUCCESS('🚀 Iniciando envío de mensaje masivo')
        )
        self.stdout.write('=' * 60)

        # Obtener usuarios según la plataforma
        if platform == 'WHATSAPP':
            users = User.objects.filter(
                platform__in=['WHATSAPP', 'MULTIPLTAFORMA'],
                phone_number__isnull=False
            ).exclude(phone_number='')
        else:
            users = User.objects.filter(
                phone_number__isnull=False
            ).exclude(phone_number='')

        total_users = users.count()

        if total_users == 0:
            self.stdout.write(
                self.style.WARNING(
                    '⚠️ No se encontraron usuarios para enviar mensajes')
            )
            return

        self.stdout.write(f"👥 Total de usuarios encontrados: {total_users}")
        self.stdout.write(f"📱 Plataforma: {platform}")
        self.stdout.write(f"⏱️ Delay entre mensajes: {delay} segundos")
        self.stdout.write(f"💬 Mensaje: {message}")
        self.stdout.write("-" * 60)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    '🧪 MODO DRY-RUN - No se enviarán mensajes reales')
            )
            self.show_users_preview(users)
            return

        # Confirmar antes de enviar
        confirm = input(
            "\n¿Estás seguro de que quieres enviar este mensaje a todos los usuarios? (sí/no): ")
        if confirm.lower() not in ['sí', 'si', 'yes', 'y']:
            self.stdout.write(
                self.style.WARNING('❌ Envío cancelado por el usuario')
            )
            return

        # Enviar mensajes
        self.send_mass_messages(users, message, delay)

    def get_template_message(self, template):
        """Retorna mensajes predefinidos según el template"""
        templates = {
            'reminder': (
                "🔔 *Recordatorio de Tresqu*\n\n"
                "¡Hola! 👋 Esperamos que tengas un excelente día.\n\n"
                "💰 Recuerda registrar tus gastos e ingresos de hoy para mantener "
                "un control perfecto de tus finanzas.\n\n"
                "Simplemente envíame un mensaje como:\n"
                "• \"Gasté 25000 en almuerzo\"\n"
                "• \"Compré café por 5000\"\n"
                "• \"Gané 50000 en mi negocio\"\n\n"
                "📊 Revisa tu dashboard en https://tresqu.com\n\n"
                "¡Tus finanzas bajo control! 💪"
            ),
            'welcome': (
                "🎉 *¡Bienvenido a Tresqu!*\n\n"
                "Gracias por unirte a nuestra comunidad de control financiero. "
                "Estamos aquí para ayudarte a gestionar tus gastos e ingresos de manera inteligente.\n\n"
                "💡 Puedes empezar registrando tus movimientos enviándome mensajes como:\n"
                "• \"Gasté 30000 en supermercado\"\n"
                "• \"Recibí 100000 de mi trabajo\"\n\n"
                "¡Comencemos este viaje financiero juntos! 🚀"
            ),
            'custom': ""
        }
        return templates.get(template, "")

    def show_users_preview(self, users):
        """Muestra una vista previa de los usuarios que recibirían el mensaje"""
        self.stdout.write("\n👥 Usuarios que recibirían el mensaje:")
        self.stdout.write("-" * 40)

        for i, user in enumerate(users[:10], 1):
            platform_emoji = "📱" if user.platform == "WHATSAPP" else "🔄"
            self.stdout.write(
                f"{i}. {platform_emoji} {user.first_name} - {user.phone_number} ({user.platform})"
            )

        if users.count() > 10:
            remaining = users.count() - 10
            self.stdout.write(f"... y {remaining} usuarios más")

    def send_mass_messages(self, users, message, delay):
        """Envía el mensaje a todos los usuarios"""
        sent_count = 0
        failed_count = 0
        total_users = users.count()

        self.stdout.write(f"\n📤 Iniciando envío a {total_users} usuarios...")
        self.stdout.write("-" * 50)

        for i, user in enumerate(users, 1):
            try:
                # Personalizar mensaje con el nombre del usuario
                personalized_message = message.replace(
                    "{name}", user.first_name or "")

                # Enviar mensaje usando Meta WhatsApp API
                success = send_meta_whatsapp_message(
                    phone_number=user.phone_number,
                    message_text=personalized_message
                )

                if success:
                    sent_count += 1
                    status = "✅"
                    self.stdout.write(
                        f"{status} [{i}/{total_users}] {user.first_name} ({user.phone_number})"
                    )
                else:
                    failed_count += 1
                    status = "❌"
                    self.stdout.write(
                        self.style.ERROR(
                            f"{status} [{i}/{total_users}] FALLÓ: {user.first_name} ({user.phone_number})"
                        )
                    )

                # Delay entre mensajes para evitar rate limiting
                if i < total_users:  # No hacer delay después del último mensaje
                    time.sleep(delay)

            except Exception as e:
                failed_count += 1
                self.stdout.write(
                    self.style.ERROR(
                        f"❌ [{i}/{total_users}] ERROR: {user.first_name} - {str(e)}"
                    )
                )
                # Continuar con el siguiente usuario
                continue

        # Resumen final
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            self.style.SUCCESS(f"📊 RESUMEN DEL ENVÍO MASIVO")
        )
        self.stdout.write(f"✅ Mensajes enviados exitosamente: {sent_count}")
        self.stdout.write(f"❌ Mensajes fallidos: {failed_count}")
        self.stdout.write(f"📱 Total de usuarios procesados: {total_users}")

        success_rate = (sent_count / total_users *
                        100) if total_users > 0 else 0
        self.stdout.write(f"📈 Tasa de éxito: {success_rate:.1f}%")

        if failed_count > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"\n⚠️ {failed_count} mensajes fallaron. Revisa los logs para más detalles."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "\n🎉 ¡Todos los mensajes se enviaron exitosamente!")
            )
