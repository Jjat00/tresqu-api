"""
Comando de Django para configurar Meta WhatsApp API
Uso: python manage.py setup_meta_whatsapp
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from whatsappbot.meta_setup import MetaWhatsAppSetup


class Command(BaseCommand):
    help = 'Configura automáticamente Meta WhatsApp API webhook y conexiones'

    def add_arguments(self, parser):
        parser.add_argument(
            '--webhook-url',
            type=str,
            help='URL del webhook (por defecto usa la configurada en meta_setup.py)',
        )
        parser.add_argument(
            '--test-only',
            action='store_true',
            help='Solo probar la verificación del webhook sin configurar',
        )
        parser.add_argument(
            '--show-config',
            action='store_true',
            help='Mostrar la configuración actual sin ejecutar setup',
        )

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS('🚀 Configurador de Meta WhatsApp API')
        )
        self.stdout.write('=' * 50)

        # Crear instancia del configurador
        setup = MetaWhatsAppSetup()

        # Cambiar URL del webhook si se proporciona
        if options['webhook_url']:
            setup.webhook_url = options['webhook_url']
            self.stdout.write(
                f"📍 Usando URL personalizada: {setup.webhook_url}")

        # Mostrar configuración actual
        if options['show_config']:
            self.show_current_config(setup)
            return

        # Solo probar verificación
        if options['test_only']:
            self.stdout.write(
                "\n🧪 Modo de prueba - Solo verificación del webhook")
            if setup.test_webhook_verification():
                self.stdout.write(
                    self.style.SUCCESS('✅ Verificación del webhook exitosa')
                )
            else:
                self.stdout.write(
                    self.style.ERROR('❌ Verificación del webhook falló')
                )
            return

        # Ejecutar configuración completa
        self.stdout.write("\n🔧 Ejecutando configuración completa...")

        try:
            success = setup.full_setup()

            if success:
                self.stdout.write(
                    self.style.SUCCESS(
                        '\n🎉 ¡Configuración completada exitosamente!')
                )
                self.stdout.write(
                    self.style.WARNING(
                        '\n⚠️ Recuerda configurar las variables de entorno en tu servidor de producción')
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        '\n❌ La configuración falló. Revisa los logs para más detalles.')
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(
                    f'\n💥 Error durante la configuración: {str(e)}')
            )

    def show_current_config(self, setup):
        """Muestra la configuración actual"""
        self.stdout.write("\n📋 Configuración actual:")
        self.stdout.write("-" * 30)

        config_items = [
            ("URL del webhook", setup.webhook_url),
            ("Token de verificación", setup.verify_token),
            ("Access Token", "***" +
             setup.access_token[-10:] if setup.access_token else "❌ No configurado"),
            ("App ID", setup.app_id or "❌ No configurado"),
            ("WABA ID", setup.waba_id or "❌ No configurado"),
        ]

        for label, value in config_items:
            status = "✅" if value and not value.startswith("❌") else "❌"
            self.stdout.write(f"{status} {label}: {value}")

        # Verificar si la configuración está completa
        if all([setup.access_token, setup.app_id, setup.waba_id]):
            self.stdout.write(
                self.style.SUCCESS(
                    "\n✅ Configuración completa - Lista para usar")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠️ Configuración incompleta - Revisa las variables de entorno")
            )
            self.stdout.write("\nVariables de entorno requeridas:")
            self.stdout.write("- META_WHATSAPP_ACCESS_TOKEN")
            self.stdout.write("- META_APP_ID")
            self.stdout.write("- META_WHATSAPP_BUSINESS_ACCOUNT_ID")
            self.stdout.write("- META_WHATSAPP_PHONE_NUMBER_ID")
            self.stdout.write("- META_WHATSAPP_VERIFY_TOKEN (opcional)")
