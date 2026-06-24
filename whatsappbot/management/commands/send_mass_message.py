"""
Comando de Django para enviar mensajes masivos a usuarios de WhatsApp
Uso: 
- Mensaje de texto: python manage.py send_mass_message --message "Tu mensaje aquí"
- Template: python manage.py send_mass_message --template-name "audio_feature" --template-language "es"
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from users.models import User
from whatsappbot.views import send_meta_whatsapp_message
import time
import logging
import requests
import json

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Envía un mensaje masivo a todos los usuarios de WhatsApp registrados'

    def add_arguments(self, parser):
        parser.add_argument(
            '--message',
            type=str,
            help='Mensaje de texto a enviar a todos los usuarios',
        )
        parser.add_argument(
            '--template-name',
            type=str,
            help='Nombre del template de WhatsApp a usar',
        )
        parser.add_argument(
            '--template-language',
            type=str,
            default='es',
            help='Código del idioma para el template (por defecto: es)',
        )
        parser.add_argument(
            '--template-params',
            type=str,
            help='Parámetros del template en formato JSON (opcional)',
        )
        parser.add_argument(
            '--exclude-numbers',
            type=str,
            help='Lista de números a excluir separados por comas (ej: 573164277879,573123456789)',
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
            help='Usar un template predefinido de texto (deprecated, usar --template-name)',
        )

    def handle(self, *args, **options):
        message = options['message']
        template_name = options['template_name']
        template_language = options['template_language']
        template_params = options['template_params']
        exclude_numbers = options['exclude_numbers']
        platform = options['platform']
        delay = options['delay']
        dry_run = options['dry_run']
        template = options['template']

        # Validar que se proporcione mensaje o template
        if not message and not template_name and not template:
            self.stdout.write(
                self.style.ERROR(
                    '❌ Debes proporcionar --message o --template-name'
                )
            )
            return

        # Procesar números a excluir
        excluded_numbers = []
        if exclude_numbers:
            excluded_numbers = [num.strip()
                                for num in exclude_numbers.split(',')]
            self.stdout.write(f"🚫 Números excluidos: {excluded_numbers}")

        # Usar template predefinido si se especifica (deprecated)
        if template:
            message = self.get_template_message(template)
            self.stdout.write(f"📝 Usando template '{template}': {message}")

        # Procesar parámetros del template
        template_parameters = None
        if template_params:
            try:
                template_parameters = json.loads(template_params)
            except json.JSONDecodeError:
                self.stdout.write(
                    self.style.ERROR(
                        '❌ Los parámetros del template deben estar en formato JSON válido'
                    )
                )
                return

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

        # Excluir números específicos
        if excluded_numbers:
            users = users.exclude(phone_number__in=excluded_numbers)

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

        if template_name:
            self.stdout.write(
                f"📋 Template: {template_name} (idioma: {template_language})")
            if template_parameters:
                self.stdout.write(f"🔧 Parámetros: {template_parameters}")
        else:
            self.stdout.write(f"💬 Mensaje: {message}")

        if excluded_numbers:
            self.stdout.write(f"🚫 Números excluidos: {len(excluded_numbers)}")

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
        if template_name:
            self.send_mass_templates(
                users, template_name, template_language, template_parameters, delay)
        else:
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
                "📊 Revisa tu dashboard en https://tresqu.com/dashboard/home\n\n"
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
        self.show_final_summary(sent_count, failed_count, total_users)

    def send_meta_whatsapp_template(self, phone_number, template_name, language_code, parameters=None):
        """Envía un template de WhatsApp usando la API de Meta"""
        try:
            url = f"https://graph.facebook.com/v22.0/{settings.META_WHATSAPP_PHONE_NUMBER_ID}/messages"

            payload = {
                "messaging_product": "whatsapp",
                "to": phone_number,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {
                        "code": language_code
                    }
                }
            }

            # Agregar parámetros si se proporcionan
            if parameters:
                payload["template"]["components"] = parameters

            headers = {
                "Authorization": f"Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            }

            response = requests.post(url, headers=headers, json=payload)

            if response.status_code == 200:
                return True
            else:
                logger.error(
                    f"Error enviando template a {phone_number}: {response.text}")
                return False

        except Exception as e:
            logger.error(
                f"Excepción enviando template a {phone_number}: {str(e)}")
            return False

    def send_mass_templates(self, users, template_name, language_code, parameters, delay):
        """Envía templates de WhatsApp a todos los usuarios"""
        sent_count = 0
        failed_count = 0
        total_users = users.count()

        self.stdout.write(
            f"\n📤 Iniciando envío de template '{template_name}' a {total_users} usuarios...")
        self.stdout.write("-" * 50)

        for i, user in enumerate(users, 1):
            try:
                # Enviar template usando Meta WhatsApp API
                success = self.send_meta_whatsapp_template(
                    phone_number=user.phone_number,
                    template_name=template_name,
                    language_code=language_code,
                    parameters=parameters
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
        self.show_final_summary(sent_count, failed_count, total_users)

    def show_final_summary(self, sent_count, failed_count, total_users):
        """Muestra el resumen final del envío"""
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
