#!/usr/bin/env python3
"""
Script para limpiar el caché de mensajes de voz que puedan estar interfiriendo
Uso: python whatsappbot/clear_voice_cache.py
"""

from django.core.cache import cache
import os
import sys
import django
from pathlib import Path
import re

# Configurar Django
sys.path.append(str(Path(__file__).parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cashbotapp.settings')
django.setup()

# Importar después de configurar Django


def clear_voice_message_cache():
    """
    Limpia el caché de mensajes de voz
    """
    print("🧹 Limpiando caché de mensajes de voz...")

    # Limpiar caché de Django
    try:
        # Intentar limpiar todo el caché
        cache.clear()
        print("   ✅ Caché de Django limpiado completamente")
    except Exception as e:
        print(f"   ⚠️ No se pudo limpiar todo el caché de Django: {e}")

    print("   ✅ Limpieza de caché completada")


def show_cache_info():
    """
    Muestra información sobre el caché actual
    """
    print("\n📊 Información del caché:")

    from django.conf import settings
    cache_config = getattr(settings, 'CACHES', {})
    default_cache = cache_config.get('default', {})

    print(f"   • Backend: {default_cache.get('BACKEND', 'No configurado')}")
    print(
        f"   • Ubicación: {default_cache.get('LOCATION', 'No especificada')}")


def test_cache_functionality():
    """
    Prueba la funcionalidad básica del caché
    """
    print("\n🧪 Probando funcionalidad del caché...")

    test_key = "test_voice_cache_key"
    test_value = "test_value_123"

    try:
        # Probar escritura
        cache.set(test_key, test_value, 60)
        print("   ✅ Escritura en caché exitosa")

        # Probar lectura
        retrieved_value = cache.get(test_key)
        if retrieved_value == test_value:
            print("   ✅ Lectura de caché exitosa")
        else:
            print(
                f"   ❌ Error en lectura: esperado '{test_value}', obtenido '{retrieved_value}'")

        # Probar eliminación
        cache.delete(test_key)
        final_value = cache.get(test_key)
        if final_value is None:
            print("   ✅ Eliminación de caché exitosa")
        else:
            print(
                f"   ❌ Error en eliminación: valor aún existe: '{final_value}'")

    except Exception as e:
        print(f"   ❌ Error probando caché: {e}")


def main():
    print("🤖 WhatsApp Bot - Limpieza de Caché de Mensajes de Voz")
    print("=" * 60)

    # Mostrar información del caché
    show_cache_info()

    # Probar funcionalidad del caché
    test_cache_functionality()

    # Limpiar caché de mensajes de voz
    clear_voice_message_cache()

    print("\n💡 Recomendaciones:")
    print("   1. Reinicia el servidor Django después de limpiar el caché")
    print("   2. Prueba enviar un nuevo mensaje de voz")
    print("   3. Monitorea los logs para verificar el procesamiento correcto")

    print("\n🚀 El caché ha sido limpiado. ¡Listo para probar mensajes de voz!")


if __name__ == "__main__":
    main()
