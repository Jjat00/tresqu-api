# Gmail Integration

**Deteccion automatica de compras**

App Django que conecta cuentas de Gmail via OAuth2 para detectar correos de compras automaticamente usando IA (LangChain + GPT-4.1). Crea gastos automaticos y coordina con WhatsApp para categorizacion.

## Arquitectura

```
[Gmail] ──Push──> [Pub/Sub] ──Webhook──> [Django gmailbot]
                                              │
                                    ┌─────────┼──────────┐
                                    │         │          │
                              [AI Parser] [Expense DB] [WhatsApp]
                                    │         │          │
                                    └────>  Gasto    Pregunta
                                         creado    categoria
                                              │          │
                                              │<── Respuesta ──│
                                              │          │
                                         Categorizado  Confirmacion
```

### Flujo completo

1. El usuario conecta su cuenta de Gmail via OAuth2
2. Se crea un Gmail Watch con Pub/Sub para recibir notificaciones push
3. Cuando llega un correo nuevo, Google envia una notificacion al webhook
4. El sistema obtiene el correo via Gmail API y lo procesa con IA
5. Si el correo es de una compra, se crea un `Expense` con categoria "Sin Categorizar"
6. Se envia un mensaje por WhatsApp preguntando la categoria
7. El usuario responde y la IA interpreta la respuesta
8. El gasto se actualiza con la categoria correcta y se confirma por WhatsApp

## Modelos

| Modelo | Descripcion |
|--------|-------------|
| `GoogleAccount` | Almacena tokens OAuth2 cifrados con Fernet, vinculados al usuario |
| `GmailWatch` | Suscripcion push de Gmail (expira cada 7 dias, se renueva automaticamente) |
| `ProcessedEmail` | Historial de emails procesados, incluye deduplicacion por `message_id` |

## Modulos

| Archivo | Funcion |
|---------|---------|
| `encryption.py` | Cifrado y descifrado Fernet de tokens OAuth |
| `oauth.py` | Flujo OAuth2: generar URL de autorizacion, intercambiar codigo, refrescar token, revocar acceso |
| `gmail_service.py` | Interaccion con Gmail API: crear watch, obtener historial, leer mensajes, extraer texto |
| `email_processor.py` | Pipeline de IA: parseo de correos de compras, creacion de gastos, notificacion por WhatsApp |
| `whatsapp_handler.py` | Manejo de respuestas de categorizacion recibidas via WhatsApp |
| `views.py` | Endpoints REST y webhook de Pub/Sub |
| `serializers.py` | Serializadores DRF para los modelos |
| `admin.py` | Configuracion del admin de Django |

## Endpoints

| Metodo | URL | Auth | Descripcion |
|--------|-----|------|-------------|
| GET | `/api/gmail/oauth/url/` | JWT | Genera URL de autorizacion de Google |
| GET | `/api/gmail/oauth/callback/` | - | Callback OAuth, guarda tokens cifrados, redirige al frontend |
| POST | `/api/gmail/disconnect/` | JWT | Desconecta la cuenta de Gmail del usuario |
| GET | `/api/gmail/status/` | JWT | Estado de conexion + estadisticas de emails procesados |
| GET | `/api/gmail/processed-emails/` | JWT | Lista paginada de emails procesados |
| POST | `/api/gmail/sync/` | JWT | Sincronizacion manual de correos recientes |
| POST | `/gmail/webhook/` | - | Webhook Pub/Sub (recibe notificaciones de Google Cloud) |

## Configuracion Google Cloud

Para configurar la integracion con Gmail es necesario:

1. Crear un proyecto en Google Cloud Console
2. Habilitar las APIs de Gmail y Pub/Sub
3. Configurar las credenciales OAuth2 (Client ID y Client Secret)
4. Crear un topic de Pub/Sub y una suscripcion push apuntando al webhook
5. Configurar las variables de entorno correspondientes en `.env`

Consulta **[docs/GMAIL_SETUP_GUIDE.md](../docs/GMAIL_SETUP_GUIDE.md)** para la guia paso a paso completa con capturas y troubleshooting.

## Management Commands

```bash
# Renovar watches que expiran en menos de 24 horas
python manage.py renew_gmail_watches

# Sincronizacion manual para un usuario especifico
python manage.py gmail_manual_sync --user_id 31

# Sincronizacion manual para todos los usuarios conectados
python manage.py gmail_manual_sync --all
```

## Seguridad

- **Tokens cifrados**: Los tokens OAuth2 se almacenan cifrados con Fernet (AES-128-CBC). La clave de cifrado se configura en `GMAIL_TOKEN_ENCRYPTION_KEY`
- **Scope minimo**: Solo se solicita el permiso `gmail.readonly` para leer correos
- **State en OAuth**: Se usa un parametro `state` para identificar al usuario durante el flujo de autorizacion
- **Webhook sin JWT**: El webhook de Pub/Sub no requiere autenticacion JWT (las peticiones vienen de Google), pero se valida el formato esperado de Pub/Sub
- **Contenido truncado**: El contenido de los emails se trunca a 3000 caracteres antes de enviarlo a la IA

## Flujo de categorizacion WhatsApp

```
1. Se detecta compra en email
   └──> Se crea Expense con categoria "Sin Categorizar"

2. Se envia mensaje WhatsApp:
   "Detectamos una compra de $X en Y. ¿De que fue?"

3. Usuario responde con la categoria
   └──> AI interpreta la respuesta

4. Se actualiza el Expense con la categoria correcta

5. Se confirma por WhatsApp:
   "Listo, tu gasto de $X fue categorizado como Z"
```

## Variables de entorno requeridas

```bash
GOOGLE_CLIENT_ID=           # Client ID de Google Cloud
GOOGLE_CLIENT_SECRET=       # Client Secret de Google Cloud
GOOGLE_REDIRECT_URI=        # URL de callback OAuth
GOOGLE_PUBSUB_TOPIC=        # Topic de Pub/Sub (projects/xxx/topics/yyy)
GOOGLE_CLOUD_PROJECT_ID=    # ID del proyecto en Google Cloud
GMAIL_TOKEN_ENCRYPTION_KEY= # Clave Fernet para cifrar tokens
```
