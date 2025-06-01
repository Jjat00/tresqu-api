"""
Ejemplos de uso para el sistema de mensajería masiva de WhatsApp
Este archivo muestra cómo usar las nuevas funcionalidades para evitar errores 131047
"""

import requests
import json

# Configuración
API_BASE_URL = "http://localhost:8000/whatsapp"
API_KEY = "admin_secret_key"  # Cambiar por tu API key real

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}


def send_mass_message_with_text():
    """
    Ejemplo: Enviar mensaje masivo con texto normal
    NOTA: Esto puede generar errores 131047 si los usuarios no han respondido en 24h
    """
    data = {
        "message": "¡Hola {name}! 👋 Recuerda registrar tus gastos de hoy en Tresqu.",
        "platform": "WHATSAPP",
        "dry_run": False
    }

    response = requests.post(
        f"{API_BASE_URL}/send-mass-message/",
        headers=headers,
        json=data
    )

    print("Respuesta mensaje masivo con texto:")
    print(json.dumps(response.json(), indent=2))


def send_mass_message_with_template():
    """
    Ejemplo: Enviar mensaje masivo usando plantilla de Meta
    RECOMENDADO: Esto evita errores 131047 para usuarios inactivos
    """
    data = {
        "use_template": True,
        "template_name": "reminder_daily",  # Nombre de tu plantilla aprobada en Meta
        "template_language": "es",
        "template_params": ["Usuario"],  # Parámetros para la plantilla
        "platform": "WHATSAPP",
        "dry_run": False
    }

    response = requests.post(
        f"{API_BASE_URL}/send-mass-message/",
        headers=headers,
        json=data
    )

    print("Respuesta mensaje masivo con plantilla:")
    print(json.dumps(response.json(), indent=2))


def send_template_to_specific_numbers():
    """
    Ejemplo: Enviar plantilla a números específicos
    """
    data = {
        "template_name": "welcome_message",
        "template_language": "es",
        "template_params": ["Nuevo Usuario", "Tresqu"],
        "phone_numbers": [
            "573001234567",
            "573007654321"
        ],
        "dry_run": False
    }

    response = requests.post(
        f"{API_BASE_URL}/send-template/",
        headers=headers,
        json=data
    )

    print("Respuesta plantilla a números específicos:")
    print(json.dumps(response.json(), indent=2))


def preview_mass_message():
    """
    Ejemplo: Vista previa de mensaje masivo (dry run)
    """
    data = {
        "message": "Mensaje de prueba para {name}",
        "platform": "WHATSAPP",
        "dry_run": True
    }

    response = requests.post(
        f"{API_BASE_URL}/send-mass-message/",
        headers=headers,
        json=data
    )

    print("Vista previa mensaje masivo:")
    print(json.dumps(response.json(), indent=2))


def schedule_reminder_with_template():
    """
    Ejemplo: Programar recordatorios usando plantillas predefinidas
    """
    data = {
        "type": "daily",
        "template": "reminder"
    }

    response = requests.post(
        f"{API_BASE_URL}/schedule-reminders/",
        headers=headers,
        json=data
    )

    print("Respuesta recordatorio programado:")
    print(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    print("=== Ejemplos de uso del sistema de mensajería WhatsApp ===\n")

    print("1. Vista previa de mensaje masivo:")
    preview_mass_message()
    print("\n" + "="*50 + "\n")

    print("2. Mensaje masivo con plantilla (RECOMENDADO):")
    # send_mass_message_with_template()
    print("Comentado para evitar envío real")
    print("\n" + "="*50 + "\n")

    print("3. Plantilla a números específicos:")
    # send_template_to_specific_numbers()
    print("Comentado para evitar envío real")
    print("\n" + "="*50 + "\n")

    print("4. Mensaje masivo con texto (puede generar errores 131047):")
    # send_mass_message_with_text()
    print("Comentado para evitar envío real")
    print("\n" + "="*50 + "\n")

    print("5. Programar recordatorios:")
    # schedule_reminder_with_template()
    print("Comentado para evitar envío real")


"""
PLANTILLAS DE MENSAJE RECOMENDADAS PARA META:

1. Plantilla de Recordatorio Diario:
   Nombre: reminder_daily
   Categoría: UTILITY
   Contenido: "¡Hola {{1}}! 👋 Recuerda registrar tus gastos de hoy en Tresqu para mantener el control de tus finanzas. 💰"

2. Plantilla de Bienvenida:
   Nombre: welcome_message
   Categoría: UTILITY
   Contenido: "¡Bienvenido a {{2}}, {{1}}! 🎉 Estamos aquí para ayudarte a gestionar tus finanzas de manera inteligente."

3. Plantilla de Resumen Semanal:
   Nombre: weekly_summary
   Categoría: UTILITY
   Contenido: "📊 Hola {{1}}, es momento de revisar tu resumen semanal en {{2}}. ¿Cómo van tus finanzas esta semana?"

NOTAS IMPORTANTES:
- Las plantillas deben ser aprobadas por Meta antes de usarse
- Usar plantillas evita errores 131047 (Re-engagement message)
- Las plantillas son la única forma de contactar usuarios después de 24h de inactividad
- Los parámetros se numeran como {{1}}, {{2}}, etc. en Meta
"""
