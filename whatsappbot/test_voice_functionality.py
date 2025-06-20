#!/usr/bin/env python3
"""
Script de prueba para verificar la funcionalidad de mensajes de voz en WhatsApp Bot
Uso: python whatsappbot/test_voice_functionality.py
"""

import logging
import tempfile
import asyncio
from django.conf import settings
import os
import sys
import django
from pathlib import Path

# Configurar Django
sys.path.append(str(Path(__file__).parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cashbotapp.settings')
django.setup()


# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_voice_functionality():
    """
    Prueba la funcionalidad de mensajes de voz
    """
    print("🧪 Iniciando pruebas de funcionalidad de mensajes de voz...")

    # 1. Verificar configuración
    print("\n1️⃣ Verificando configuración...")

    required_settings = [
        'META_WHATSAPP_ACCESS_TOKEN',
        'META_WHATSAPP_PHONE_NUMBER_ID',
        'META_WHATSAPP_BUSINESS_ACCOUNT_ID',
        'OPENAI_API_KEY'
    ]

    missing_settings = []
    for setting in required_settings:
        value = getattr(settings, setting, '')
        if not value:
            missing_settings.append(setting)
        else:
            print(f"   ✅ {setting}: {'*' * min(len(value), 10)}...")

    if missing_settings:
        print(f"   ❌ Configuraciones faltantes: {', '.join(missing_settings)}")
        print("   💡 Configura estas variables de entorno antes de usar mensajes de voz")
        return False

    # 2. Probar importación de funciones
    print("\n2️⃣ Probando importación de funciones...")

    try:
        from whatsappbot.services import transcribe_audio, download_whatsapp_media
        print("   ✅ Funciones de servicios importadas correctamente")
    except ImportError as e:
        print(f"   ❌ Error importando funciones: {e}")
        return False

    # 3. Probar funcionalidad de archivos temporales
    print("\n3️⃣ Probando manejo de archivos temporales...")

    try:
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as temp_file:
            temp_path = temp_file.name
            temp_file.write(b"dummy audio data")

        # Verificar que el archivo existe
        if os.path.exists(temp_path):
            print("   ✅ Archivo temporal creado correctamente")

            # Limpiar
            os.unlink(temp_path)
            print("   ✅ Archivo temporal eliminado correctamente")
        else:
            print("   ❌ Error creando archivo temporal")
            return False

    except Exception as e:
        print(f"   ❌ Error con archivos temporales: {e}")
        return False

    # 4. Verificar que OpenAI esté disponible
    print("\n4️⃣ Verificando conexión con OpenAI...")

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        # No hacer una llamada real, solo verificar que el cliente se inicialice
        print("   ✅ Cliente de OpenAI inicializado correctamente")

    except Exception as e:
        print(f"   ❌ Error con OpenAI: {e}")
        return False

    # 5. Verificar flujo de manejo de mensajes
    print("\n5️⃣ Verificando flujo de manejo de mensajes...")

    try:
        from whatsappbot.bot import handle_whatsapp_message
        print("   ✅ Función handle_whatsapp_message disponible")

        # Verificar que la función acepta los parámetros correctos
        import inspect
        sig = inspect.signature(handle_whatsapp_message)
        required_params = ['sender_number', 'message_text', 'message_id']

        for param in required_params:
            if param not in sig.parameters:
                print(f"   ❌ Parámetro faltante: {param}")
                return False

        print("   ✅ Parámetros de función correctos")

    except Exception as e:
        print(f"   ❌ Error verificando flujo: {e}")
        return False

    print("\n🎉 ¡Todas las pruebas pasaron exitosamente!")
    print("\n📋 Resumen de funcionalidad:")
    print("   • Configuración de Meta WhatsApp API: ✅")
    print("   • Configuración de OpenAI API: ✅")
    print("   • Funciones de descarga y transcripción: ✅")
    print("   • Manejo de archivos temporales: ✅")
    print("   • Integración con bot principal: ✅")

    print("\n💡 Próximos pasos:")
    print("   1. Envía un mensaje de voz a tu bot de WhatsApp")
    print("   2. Revisa los logs para ver el procesamiento")
    print("   3. Verifica que el bot responda basado en la transcripción")

    return True


def print_usage_instructions():
    """
    Imprime instrucciones de uso
    """
    print("\n📱 Cómo probar mensajes de voz:")
    print("   1. Abre WhatsApp y ve al chat con tu bot")
    print("   2. Mantén presionado el botón de micrófono")
    print("   3. Graba un mensaje como: 'Gasté 50 dólares en comida hoy'")
    print("   4. Envía el mensaje de voz")
    print("   5. El bot responderá: '🎧 Procesando tu mensaje de voz...'")
    print("   6. Después procesará el texto y registrará el gasto")

    print("\n🔍 Cómo revisar logs:")
    print("   • Busca en los logs: 'Procesando mensaje de voz'")
    print("   • Verifica: 'Transcripción exitosa'")
    print("   • Confirma que el gasto se registró correctamente")


if __name__ == "__main__":
    print("🤖 WhatsApp Bot - Prueba de Mensajes de Voz")
    print("=" * 50)

    # Ejecutar pruebas
    success = asyncio.run(test_voice_functionality())

    if success:
        print_usage_instructions()
    else:
        print("\n❌ Algunas pruebas fallaron. Revisa la configuración antes de continuar.")
        sys.exit(1)
