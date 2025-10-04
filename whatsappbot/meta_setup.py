"""
Script de utilidad para configurar Meta WhatsApp API
Automatiza los pasos de configuración del webhook
"""

import requests
import json
import os
from django.conf import settings


class MetaWhatsAppSetup:
    def __init__(self):
        self.access_token = getattr(settings, 'META_WHATSAPP_ACCESS_TOKEN', '')
        self.verify_token = getattr(
            settings, 'META_WHATSAPP_VERIFY_TOKEN', 'mi_token_secreto')
        self.app_id = getattr(settings, 'META_APP_ID', '')
        self.waba_id = getattr(
            settings, 'META_WHATSAPP_BUSINESS_ACCOUNT_ID', '')
        self.webhook_url = "https://tresqu.com/whatsapp/webhook/"  # URL simplificada

    def setup_app_webhook(self):
        """
        Paso 2: Agrega el webhook a tu App de Meta
        """
        url = f"https://graph.facebook.com/v23.0/{self.app_id}/subscriptions"

        payload = {
            "object": "whatsapp_business_account",
            "callback_url": self.webhook_url,
            "fields": "messages",
            "verify_token": self.verify_token,
            "access_token": self.access_token
        }

        headers = {
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                print("✅ Webhook agregado exitosamente a la App de Meta")
                print(f"Respuesta: {response.json()}")
                return True
            else:
                print(f"❌ Error agregando webhook: {response.status_code}")
                print(f"Respuesta: {response.text}")
                return False

        except Exception as e:
            print(f"❌ Error en la petición: {str(e)}")
            return False

    def connect_waba_to_app(self):
        """
        Paso 3: Conecta la App al WhatsApp Business Account
        """
        url = f"https://graph.facebook.com/v23.0/{self.waba_id}/subscribed_apps"

        payload = {
            "subscribed_fields": ["messages"],
            "access_token": self.access_token
        }

        headers = {
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                print("✅ WhatsApp Business Account conectado exitosamente a la App")
                print(f"Respuesta: {response.json()}")
                return True
            else:
                print(f"❌ Error conectando WABA: {response.status_code}")
                print(f"Respuesta: {response.text}")
                return False

        except Exception as e:
            print(f"❌ Error en la petición: {str(e)}")
            return False

    def get_phone_numbers(self):
        """
        Obtiene los números de teléfono registrados en el WABA
        """
        url = f"https://graph.facebook.com/v23.0/{self.waba_id}/phone_numbers"

        params = {
            'access_token': self.access_token
        }

        try:
            response = requests.get(url, params=params)

            if response.status_code == 200:
                data = response.json()
                print("📱 Números de teléfono registrados:")
                for phone in data.get('data', []):
                    print(
                        f"  - {phone.get('display_phone_number')} (ID: {phone.get('id')})")
                    print(f"    Estado: {phone.get('status')}")
                    print(f"    Verificado: {phone.get('verified_name')}")
                return data
            else:
                print(f"❌ Error obteniendo números: {response.status_code}")
                print(f"Respuesta: {response.text}")
                return None

        except Exception as e:
            print(f"❌ Error en la petición: {str(e)}")
            return None

    def test_webhook_verification(self):
        """
        Prueba la verificación del webhook
        """
        test_url = f"{self.webhook_url}?hub.mode=subscribe&hub.verify_token={self.verify_token}&hub.challenge=test123"

        try:
            response = requests.get(test_url)

            if response.status_code == 200 and response.text == "test123":
                print("✅ Verificación del webhook funcionando correctamente")
                return True
            else:
                print(
                    f"❌ Error en verificación del webhook: {response.status_code}")
                print(f"Respuesta: {response.text}")
                return False

        except Exception as e:
            print(f"❌ Error probando webhook: {str(e)}")
            return False

    def full_setup(self):
        """
        Ejecuta la configuración completa
        """
        print("🚀 Iniciando configuración de Meta WhatsApp API...")
        print(f"📍 URL del webhook: {self.webhook_url}")
        print(f"🔑 Token de verificación: {self.verify_token}")
        print(f"📱 App ID: {self.app_id}")
        print(f"🏢 WABA ID: {self.waba_id}")
        print("-" * 50)

        # Verificar configuración
        if not all([self.access_token, self.app_id, self.waba_id]):
            print("❌ Configuración incompleta. Verifica las variables de entorno:")
            print(
                f"  - META_WHATSAPP_ACCESS_TOKEN: {'✅' if self.access_token else '❌'}")
            print(f"  - META_APP_ID: {'✅' if self.app_id else '❌'}")
            print(
                f"  - META_WHATSAPP_BUSINESS_ACCOUNT_ID: {'✅' if self.waba_id else '❌'}")
            return False

        # Paso 1: Probar verificación del webhook
        print("\n1️⃣ Probando verificación del webhook...")
        if not self.test_webhook_verification():
            print("⚠️ La verificación del webhook falló. Continúa con precaución.")

        # Paso 2: Configurar webhook en la App
        print("\n2️⃣ Configurando webhook en la App de Meta...")
        if not self.setup_app_webhook():
            print("❌ Falló la configuración del webhook. Revisa los logs.")
            return False

        # Paso 3: Conectar WABA a la App
        print("\n3️⃣ Conectando WhatsApp Business Account a la App...")
        if not self.connect_waba_to_app():
            print("❌ Falló la conexión del WABA. Revisa los logs.")
            return False

        # Paso 4: Mostrar números registrados
        print("\n4️⃣ Obteniendo números de teléfono registrados...")
        self.get_phone_numbers()

        print("\n🎉 ¡Configuración completada exitosamente!")
        print("\n📋 Próximos pasos:")
        print("1. Envía un mensaje de prueba al número registrado")
        print("2. Verifica que el webhook reciba el evento")
        print("3. Confirma que el bot responda correctamente")

        return True


def run_setup():
    """
    Función principal para ejecutar la configuración
    """
    setup = MetaWhatsAppSetup()
    return setup.full_setup()


if __name__ == "__main__":
    # Configurar Django si se ejecuta directamente
    import django
    import sys
    import os

    # Agregar el directorio del proyecto al path
    sys.path.append(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))

    # Configurar Django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cashbotapp.settings')
    django.setup()

    # Ejecutar configuración
    run_setup()
