# Configuración de Meta WhatsApp API

Esta guía te ayudará a configurar la API oficial de WhatsApp de Meta para tu bot de Cashbot.

**Nota:** Este proyecto ahora usa exclusivamente la API oficial de Meta WhatsApp. Ya no se soporta Evolution API.

## 📋 Requisitos Previos

1. **Meta App creada** en [Meta for Developers](https://developers.facebook.com/)
2. **WhatsApp Business Account (WABA)** configurado
3. **Número de teléfono** verificado en WhatsApp Business
4. **Access Token** con permisos adecuados
5. **Servidor HTTPS** público para recibir webhooks

## 🔧 Variables de Entorno

Agrega estas variables a tu archivo `.env`:

```bash
META_WHATSAPP_ACCESS_TOKEN=tu_access_token_aqui
META_WHATSAPP_VERIFY_TOKEN=mi_token_secreto
META_WHATSAPP_PHONE_NUMBER_ID=tu_phone_number_id_aqui
META_WHATSAPP_BUSINESS_ACCOUNT_ID=tu_waba_id_aqui
META_APP_ID=tu_app_id_aqui
META_APP_SECRET=tu_app_secret_aqui
```

## 🚀 Configuración Automática

Ejecuta el comando de configuración:

```bash
# Mostrar configuración actual
python manage.py setup_meta_whatsapp --show-config

# Configuración completa
python manage.py setup_meta_whatsapp

# Con URL personalizada
python manage.py setup_meta_whatsapp --webhook-url https://tudominio.com/whatsapp/webhook/
```

## 📱 Endpoints Disponibles

- **Webhook Meta**: `/whatsapp/webhook/` (GET/POST)
- **Envío de código**: `/whatsapp/send-code/` (POST)
- **Verificación de código**: `/whatsapp/verify-code/` (POST)

## 🧪 Pruebas

1. Envía un mensaje al número registrado
2. Verifica logs con: `tail -f debug.log | grep "Meta"`
3. Confirma respuesta del bot

## 📚 Configuración Manual

Si prefieres configurar manualmente:

### Paso 1: Configurar Webhook en la App

```bash
curl -X POST "https://graph.facebook.com/v22.0/{APP_ID}/subscriptions" \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "callback_url": "https://tudominio.com/whatsapp/webhook/",
    "fields": "messages",
    "verify_token": "mi_token_secreto",
    "access_token": "TU_ACCESS_TOKEN"
  }'
```

### Paso 2: Conectar WABA a la App

```bash
curl -X POST "https://graph.facebook.com/v22.0/{WABA_ID}/subscribed_apps" \
  -H "Content-Type: application/json" \
  -d '{
    "subscribed_fields": ["messages"],
    "access_token": "TU_ACCESS_TOKEN"
  }'
```

## 🔍 Verificación del Webhook

El endpoint responde automáticamente a:

```
GET /whatsapp/webhook/?hub.mode=subscribe&hub.verify_token=mi_token_secreto&hub.challenge=123456
```

## 📚 Recursos Adicionales

- [Meta WhatsApp API Documentation](https://developers.facebook.com/docs/whatsapp)
- [Webhook Setup Guide](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks)
- [Rate Limits](https://developers.facebook.com/docs/whatsapp/cloud-api/overview#rate-limits)

## 🆘 Soporte

Si encuentras problemas:

1. Revisa los logs de Django
2. Verifica la configuración con `--show-config`
3. Prueba la verificación con `--test-only`
4. Consulta la documentación oficial de Meta
