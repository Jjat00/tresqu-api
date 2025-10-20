"""
Módulo para el procesamiento de imágenes de facturas y recibos en WhatsApp

Este módulo contiene funciones auxiliares para procesar imágenes enviadas por los usuarios
a través de WhatsApp, extrayendo automáticamente los gastos usando la API de visión de OpenAI.

Funcionalidades:
- Descarga de imágenes desde WhatsApp Business API
- Conversión de imágenes a formato base64 para procesamiento
- Extracción de gastos usando GPT-4o con capacidades de visión
- Integración con el flujo de procesamiento de mensajes

Ejemplo de uso:
    1. Usuario envía imagen de factura por WhatsApp
    2. Sistema detecta tipo de mensaje como "image"
    3. Descarga imagen usando download_whatsapp_image()
    4. Convierte a base64 y envía a extract_expenses_from_image()
    5. Extrae gastos en formato estructurado
    6. Procesa gastos automáticamente con el agente LLM

Formato de salida esperado:
    "[Monto] [Moneda] en [Descripción]"
    
    Ejemplo:
    "50.50 USD en Pizza Dominos"
    "120 COP en Transporte Uber"
    "25000 COP en Supermercado Exito"

Notas:
- Requiere configuración de META_WHATSAPP_ACCESS_TOKEN en settings
- Utiliza modelo GPT-4o para capacidades de visión
- Soporta formatos: JPEG, PNG, WebP
- Timeout de procesamiento: 120 segundos
"""

# Las funciones principales están en whatsappbot/services.py:
# - extract_expenses_from_image()
# - download_whatsapp_image()

# Este archivo sirve como documentación de referencia
