# Migración Gmail → Composio

Plan de migración del `gmailbot` desde la integración directa con Google
Cloud (OAuth2 + Pub/Sub push) hacia [Composio](https://composio.dev) como
proveedor de toolkits/triggers.

## Motivación

- **Google CASA bloquea**: para usar los scopes restricted/sensitive de
  Gmail necesitamos pasar la auditoría CASA. Composio ya consume Gmail
  con su CASA aprobado y heredamos el acceso.
- Eliminamos la operativa Google Cloud Console (proyecto, Pub/Sub topic,
  watch renewal, refresh tokens, Fernet encryption).
- El SDK de Composio sirve de base para integrar más toolkits (Slack,
  Notion, etc.) sin volver a montar OAuth desde cero.

## Tradeoffs aceptados

- **Latencia**: trigger Gmail de Composio es polling. El default real
  observado es **2 min** (campo `interval` del trigger config,
  configurable de 1 min hacia arriba). Vs. push casi en tiempo real
  con Pub/Sub. Aceptado como costo del CASA-bypass.
- **Re-conexión obligatoria**: los usuarios con `GoogleAccount` actual
  tendrían que re-conectar Gmail vía Composio. **No hay usuarios reales
  con Gmail conectado todavía** — sin coste de migración real.

## Estado inicial (lo que se reemplaza)

```
[Gmail] ──Push──> [Pub/Sub] ──Webhook──> [Django gmailbot]
                                              │
                                    ┌─────────┼──────────┐
                                    │         │          │
                              [AI Parser] [Expense DB] [WhatsApp]
```

## Estado final (con Composio)

```
[Gmail] ──Poll──> [Composio] ──Webhook──> [Django gmailbot]
                                              │
                                    ┌─────────┼──────────┐
                                    │         │          │
                              [AI Parser] [Expense DB] [WhatsApp]
```

El **pipeline interno no cambia**: el AI parser (`email_processor.py`),
la creación del `Expense` y el flujo de categorización por WhatsApp
(`whatsapp_handler.py`) se reutilizan tal cual.

## Plan por fases

Cada fase corresponde a una task en el TodoList de la sesión
(`#72` → `#78`).

### Fase 1 — Recon (#72) ✅

Confirmado contra docs oficiales:

- **SDK Python:** `pip install composio` (no `composio-core`; eso es TypeScript)
- **Trigger slug:** `GMAIL_NEW_GMAIL_MESSAGE`
- **Connect flow:**
  ```python
  composio = Composio()  # lee COMPOSIO_API_KEY de env
  session = composio.create(user_id=str(user.id), toolkits=["gmail"])
  cr = session.authorize("gmail", callback_url="https://tresqu.app/api/gmail/composio/callback?user_id=...")
  # cr.redirect_url → URL hosted que abre el usuario
  ```
- **Callback:** Composio redirige a `callback_url` con `?status=success&connected_account_id=ca_xxx`
- **Activar trigger:**
  ```python
  trigger = composio.triggers.create(
      slug="GMAIL_NEW_GMAIL_MESSAGE",
      user_id=str(user.id),
      trigger_config={},  # se confirma vía composio.triggers.get_type(...).config
  )
  # trigger.trigger_id → guardar
  ```
- **Webhook subscription** (one-time por proyecto, hecho a mano desde dashboard o curl):
  ```bash
  POST https://backend.composio.dev/api/v3.1/webhook_subscriptions
  X-API-KEY: ...
  { "webhook_url": "https://tresqu.app/gmail/composio-webhook/",
    "enabled_events": ["composio.trigger.message"] }
  # respuesta incluye `secret` → COMPOSIO_WEBHOOK_SECRET
  ```
- **Webhook headers entrantes:** `webhook-id`, `webhook-signature`, `webhook-timestamp`
- **Verificación de firma:** `composio.triggers.verify_webhook(id, payload, signature, timestamp, secret)` (SDK helper)
- **Payload V3 entrante (nuestro org es V3 por default):**
  ```json
  {
    "type": "composio.trigger.message",
    "metadata": {
      "trigger_slug": "GMAIL_NEW_GMAIL_MESSAGE",
      "trigger_id": "ti_xyz",
      "connected_account_id": "ca_def",
      "user_id": "31"
    },
    "data": { "subject": "...", "message_text": "...", "id": "<gmail_msg_id>", ... }
  }
  ```
  Schema exacto del `data` se inspecciona en runtime con
  `composio.triggers.get_type("GMAIL_NEW_GMAIL_MESSAGE").payload`.
- **Mapping User ↔ Composio:** usamos `str(user.id)` como `user_id` de Composio.
  Eso convierte el `metadata.user_id` del webhook en una lookup
  trivial (`User.objects.get(id=int(metadata['user_id']))`).
  `connected_account_id` se guarda como espejo local en
  `ComposioConnection` para mostrar estado en el dashboard.

### Fase 2 — Setup (#73)

- `composio` agregado a `requirements.txt`
- Env vars nuevas:
  - `COMPOSIO_API_KEY` — credencial del workspace
  - `COMPOSIO_WEBHOOK_SECRET` — para verificar firma del webhook
- Nuevo modelo `ComposioConnection(user, connected_account_id,
  trigger_id, status, created_at, updated_at)`

### Fase 3 — Connect flow (#74)

- Reemplazar `oauth.py` por endpoints contra Composio:
  - `GET /api/gmail/composio/connect-url/` → genera URL de auth alojada
  - `GET /api/gmail/composio/callback/` → persiste `connected_account_id`
- El frontend solo cambia la URL del botón "Conectar Gmail" en
  `chat-finance-bot/src/services/`.

### Fase 4 — Trigger + webhook (#75)

- Al confirmar conexión exitosa, activar el trigger Gmail para esa
  cuenta.
- Nuevo `POST /gmail/composio-webhook/`:
  - Sin JWT (callback externo)
  - Verifica firma con `COMPOSIO_WEBHOOK_SECRET`
  - Resuelve `connected_account_id` → `User`
  - Dispatcha a `email_processor.process_email_for_user(user, mensaje)`

### Fase 5 — Cleanup (#76)

**Borrar** (todo va junto en una migración drop):

- `oauth.py`, `encryption.py`, `gmail_service.py`
- Modelos `GoogleAccount`, `GmailWatch` (con migración Django)
- `management/commands/renew_gmail_watches.py`
- Webhook Pub/Sub en `views.py`
- Env vars `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
  `GOOGLE_REDIRECT_URI`, `GOOGLE_PUBSUB_TOPIC`,
  `GOOGLE_CLOUD_PROJECT_ID`, `GMAIL_TOKEN_ENCRYPTION_KEY`

**Mantener**:

- `ProcessedEmail` (modelo + dedupe por `message_id`)
- `email_processor.py` (pipeline AI)
- `whatsapp_handler.py` (categorización)

El cleanup va último: si algo falla en fase 4, el sistema viejo queda
intacto hasta el merge final.

### Fase 6 — Docs (#77)

- Reescribir `gmailbot/README.md` para reflejar Composio
- Reemplazar `docs/GMAIL_SETUP_GUIDE.md` por
  `docs/COMPOSIO_GMAIL_SETUP.md` con pasos: crear cuenta Composio,
  generar API key, activar Gmail toolkit, configurar webhook URL en
  dashboard.

### Fase 7 — Smoke test E2E (#78)

1. Conectar Gmail vía Composio desde el frontend
2. Mandar correo de compra a la cuenta conectada
3. Verificar que llega el webhook
4. Verificar que `email_processor` crea el `Expense`
5. Verificar mensaje WhatsApp pidiendo categoría
6. Responder categoría
7. Verificar `Expense` queda categorizado

## Confirmaciones del usuario al inicio de la migración

- ✅ Latencia ~2 min aceptable (motivo: bypass de CASA; real medido tras deploy)
- ✅ Tiene cuenta Composio + API key disponible
- ✅ Va a cargar `COMPOSIO_API_KEY` en Railway
- ✅ Borrar todo lo viejo (no convivencia)
- ✅ No hay usuarios reales con Gmail conectado → migración limpia
