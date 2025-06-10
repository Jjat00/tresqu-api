import logging
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db import connection
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Crear tabla de caché en producción para WhatsApp Bot'

    def handle(self, *args, **options):
        self.stdout.write("🚀 Creando tabla de caché en producción...")

        try:
            # Verificar si estamos en producción
            if settings.DEBUG:
                self.stdout.write(
                    self.style.WARNING(
                        "⚠️ Esto parece ser un entorno de desarrollo.")
                )
                self.stdout.write(
                    "   Si realmente quieres ejecutar esto en desarrollo, usa setup_cache.")
                return

            # Verificar si la tabla ya existe
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'django_cache_table'
                    )
                """)
                table_exists = cursor.fetchone()[0]

            if table_exists:
                self.stdout.write(
                    self.style.SUCCESS(
                        "✅ La tabla 'django_cache_table' ya existe en producción.")
                )
                return

            # Crear la tabla de caché
            self.stdout.write("📦 Creando tabla de caché en producción...")
            call_command('createcachetable', verbosity=2)

            # Verificar que se creó correctamente
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'django_cache_table'
                    )
                """)
                table_created = cursor.fetchone()[0]

            if table_created:
                self.stdout.write(
                    self.style.SUCCESS(
                        "✅ Tabla de caché creada exitosamente en producción!")
                )
                self.stdout.write(
                    "🔄 Los mensajes duplicados de WhatsApp ahora serán filtrados correctamente.")
            else:
                self.stdout.write(
                    self.style.ERROR("❌ Error: La tabla no se pudo crear.")
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"❌ Error creando tabla de caché: {str(e)}")
            )
            logger.exception("Error en create_production_cache command")

            # Sugerir el comando manual como respaldo
            self.stdout.write("\n💡 Como alternativa, puedes ejecutar:")
            self.stdout.write("   python manage.py createcachetable")
            self.stdout.write("   en tu servidor de producción.")
