import logging
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Configura la tabla de caché para WhatsApp Bot'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Forzar la recreación de la tabla si ya existe',
        )
        parser.add_argument(
            '--test',
            action='store_true',
            help='Probar el funcionamiento del caché después de la configuración',
        )

    def handle(self, *args, **options):
        self.stdout.write("🔧 Configurando tabla de caché para WhatsApp Bot...")

        try:
            # Verificar si la tabla ya existe
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'django_cache_table'
                    )
                """)
                table_exists = cursor.fetchone()[0]

            if table_exists and not options['force']:
                self.stdout.write(
                    self.style.WARNING(
                        "⚠️ La tabla 'django_cache_table' ya existe.")
                )
                self.stdout.write(
                    "   Usa --force para recrearla o --test para probar su funcionamiento."
                )
                return

            if table_exists and options['force']:
                self.stdout.write("🗑️ Eliminando tabla existente...")
                with connection.cursor() as cursor:
                    cursor.execute("DROP TABLE IF EXISTS django_cache_table")

            # Crear la tabla de caché
            self.stdout.write("📦 Creando tabla de caché...")
            call_command('createcachetable', verbosity=0)

            self.stdout.write(
                self.style.SUCCESS("✅ Tabla de caché creada exitosamente!")
            )

            # Probar el caché si se solicita
            if options['test']:
                self.test_cache()

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"❌ Error configurando caché: {str(e)}")
            )
            logger.exception("Error en setup_cache command")

    def test_cache(self):
        """Prueba el funcionamiento del sistema de caché"""
        self.stdout.write("🧪 Probando el sistema de caché...")

        try:
            # Probar escritura
            test_key = 'whatsapp_test_key'
            test_value = 'test_value_12345'

            cache.set(test_key, test_value, 60)
            self.stdout.write("   ✓ Escritura en caché exitosa")

            # Probar lectura
            retrieved_value = cache.get(test_key)
            if retrieved_value == test_value:
                self.stdout.write("   ✓ Lectura de caché exitosa")
            else:
                self.stdout.write(
                    self.style.ERROR("   ❌ Error en lectura de caché")
                )
                return

            # Probar eliminación
            cache.delete(test_key)
            deleted_value = cache.get(test_key)
            if deleted_value is None:
                self.stdout.write("   ✓ Eliminación de caché exitosa")
            else:
                self.stdout.write(
                    self.style.ERROR("   ❌ Error en eliminación de caché")
                )
                return

            self.stdout.write(
                self.style.SUCCESS("✅ Todos los tests de caché pasaron!")
            )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"❌ Error probando caché: {str(e)}")
            )
            logger.exception("Error en test de caché")
