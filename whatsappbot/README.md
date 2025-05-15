# Integración de WhatsApp con Evolution API

Esta aplicación proporciona endpoints para la integración con Evolution API para WhatsApp. Permite recibir y procesar eventos de WhatsApp mediante webhooks.

## Configuración

### 1. Variables de entorno

Añade las siguientes variables a tu archivo `.env`:

```
# Evolution API para WhatsApp
EVOLUTION_API_URL=https://tu-api-evolution.com
EVOLUTION_API_KEY=tu-api-key
WHATSAPP_WEBHOOK_BASE_URL=https://tu-url-publica-para-webhooks.com/whatsapp
```

### 2. Configurar el Webhook en Evolution API

Para configurar un webhook para una instancia específica de WhatsApp, puedes hacer una petición POST a la siguiente URL:

```
POST /whatsapp/webhook/config/<instance_name>/
```

Ejemplo de payload:

```json
{
  "name": "Mi Webhook",
  "url": "https://tu-url-publica-para-webhooks.com/whatsapp/webhook/<instance_name>/",
  "webhook_by_events": false,
  "webhook_base64": false,
  "events": [
    "QRCODE_UPDATED",
    "MESSAGES_UPSERT",
    "MESSAGES_UPDATE",
    "MESSAGES_DELETE",
    "SEND_MESSAGE",
    "CONNECTION_UPDATE"
  ]
}
```

### 3. Eventos Soportados

Los siguientes eventos están soportados por Evolution API:

- `APPLICATION_STARTUP`: Notifica cuando se inicia la aplicación
- `QRCODE_UPDATED`: Envía el código QR en formato base64 para escanear
- `CONNECTION_UPDATE`: Informa sobre el estado de la conexión de WhatsApp
- `MESSAGES_SET`: Envía una lista de todos los mensajes cargados en WhatsApp (ocurre solo una vez)
- `MESSAGES_UPSERT`: Notifica cuando se recibe un mensaje
- `MESSAGES_UPDATE`: Informa cuando se actualiza un mensaje
- `MESSAGES_DELETE`: Informa cuando se elimina un mensaje
- `SEND_MESSAGE`: Notifica cuando se envía un mensaje
- `CONTACTS_SET`: Realiza la carga inicial de todos los contactos (ocurre solo una vez)
- `CONTACTS_UPSERT`: Recarga todos los contactos con información adicional (ocurre solo una vez)
- `CONTACTS_UPDATE`: Informa cuando se actualiza un contacto
- `PRESENCE_UPDATE`: Informa si el usuario está en línea, realizando alguna acción como escribir o grabar
- `CHATS_SET`: Envía una lista de todos los chats cargados
- `CHATS_UPDATE`: Informa cuando se actualiza un chat
- `CHATS_UPSERT`: Envía cualquier información de chat nueva
- `CHATS_DELETE`: Notifica cuando se elimina un chat
- `GROUPS_UPSERT`: Notifica cuando se crea un grupo
- `GROUPS_UPDATE`: Notifica cuando un grupo actualiza su información
- `GROUP_PARTICIPANTS_UPDATE`: Notifica cuando ocurre una acción relacionada con un participante
- `NEW_TOKEN`: Notifica cuando se actualiza el token (jwt)

## Recepción de Eventos

Los eventos de webhook se recibirán en la siguiente URL:

```
POST /whatsapp/webhook/<instance_name>/
```

Cuando se recibe un evento, el sistema:

1. Verifica si hay una configuración de webhook para la instancia
2. Comprueba si el evento está en la lista de eventos configurados
3. Procesa el evento según su tipo

## Consultar Configuración de Webhook

Para obtener la configuración actual de un webhook para una instancia específica:

```
GET /whatsapp/webhook/config/<instance_name>/
```

## Administración

Los webhooks configurados pueden administrarse desde el panel de administración de Django:

```
/admin/whatsappbot/whatsappwebhook/
```
