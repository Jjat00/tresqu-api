# gmailbot

App Django que aloja la lógica **Gmail-específica** del pipeline de
detección automática de compras/ingresos a partir de correos.

La autenticación + recepción de eventos Gmail ya **no** vive en este
módulo: pasó a `composio_integration/` tras migrar de OAuth2 directo
+ Pub/Sub a [Composio](https://composio.dev) (motivo: bypass del CASA
de Google).

## Responsabilidades

| Componente | Archivo | Función |
|---|---|---|
| Handler Composio | `composio_handler.py` | Implementa `ToolkitHandler`: se registra en el registry de `composio_integration`, recibe webhooks ya verificados con firma HMAC y crea `ProcessedEmail` |
| Task Celery | `composio_tasks.py` | `process_gmail_message_async` — procesa un `ProcessedEmail` en background con retry exponencial |
| Pipeline AI | `composio_pipeline.py` | `process_composio_email(pe)` — orquesta AI parse, creación de `Expense`/`Income`, embedding, WhatsApp |
| Pipeline legacy reutilizado | `email_processor.py` | `parse_purchase_email`, `send_transaction_confirmation_whatsapp` y helpers reusables. Las funciones legacy `process_email_for_user`/`process_history_update` ya **no existen** |
| Handler WhatsApp | `whatsapp_handler.py` | Manda la pregunta de categoría por WhatsApp tras detectar una compra, resuelve respuestas de "quote" |
| Modelos | `models.py` | `ComposioConnection` (estado de la conexión Composio) y `ProcessedEmail` (dedupe + audit trail) |
| Signal GDPR | `signals.py` | `pre_delete(User)` revoca trigger + connected_account en Composio |
| Lista para frontend | `views.py` + `urls.py` | Solo `ProcessedEmailListView` en `GET /gmail/processed-emails/` |

## Flujo end-to-end

```
Gmail nuevo correo
       │
       ▼  (poll cada ~2 min, configurable en composio_handler.py:trigger_specs)
   Composio
       │
       ▼  (POST con firma HMAC)
  /api/integrations/gmail/composio-webhook/      ← composio_integration/views.py
       │
       ▼  (verify_webhook OK, dispatch)
GmailComposioHandler.handle_webhook              ← gmailbot/composio_handler.py
       │
       ├─ ProcessedEmail.get_or_create (idempotente)
       │
       ▼
 process_gmail_message_async.delay               ← gmailbot/composio_tasks.py
       │
       ▼  (Celery worker)
process_composio_email                           ← gmailbot/composio_pipeline.py
       │
       ├─ parse_purchase_email  (AI)              ← email_processor.py (reusado)
       ├─ Expense / Income create
       └─ send_transaction_confirmation_whatsapp  ← email_processor.py + whatsapp_handler.py
```

## Endpoints expuestos por este módulo

Solo uno (legacy del frontend, mantenido):

- `GET /gmail/processed-emails/` (JWT) — lista de `ProcessedEmail` del
  usuario para el dashboard.

Los endpoints connect/callback/disconnect/webhook viven en
`composio_integration/` bajo `/api/integrations/gmail/...`.

## Para conectar Gmail

Ver [docs/COMPOSIO_GMAIL_SETUP.md](../docs/COMPOSIO_GMAIL_SETUP.md).

## Migraciones relevantes

- `0001_initial.py` → `0003_processedemail_income_and_transaction_type.py`
  — schema legacy (con `GoogleAccount` + `GmailWatch`).
- `0004_composio_connection_and_fk_swap.py` — agrega
  `ComposioConnection`, swap del FK de `ProcessedEmail`
  (`google_account` → `user` + `composio_connection`), unique
  constraint parcial.
- `0005_drop_legacy_google_models.py` — drop de `GoogleAccount` y
  `GmailWatch`.
- `0006_remove_composioconnection_gmailbot_compconn_pending_idx_and_more.py`
  — limpieza del índice parcial `compconn_pending_idx` después de
  reorganizar el set de constraints (artefacto de Django, sin cambio
  de schema relevante para consumidores).

## Tradeoffs

- **Latencia ~2 min** entre la llegada del email a Gmail y la
  creación del `Expense`/`Income`. Es el polling default del trigger
  `GMAIL_NEW_GMAIL_MESSAGE` (se puede subir/bajar tocando el `interval`
  en `composio_handler.py:trigger_specs` y reconectando).
- El pipeline AI puede tardar varios segundos (LLM call + embedding);
  por eso se ejecuta en Celery, no en el webhook.
- `ProcessedEmail.composio_connection` es `SET_NULL`: si el usuario
  desconecta y reconecta con otra cuenta Gmail, el histórico se
  preserva pero pierde el FK al `ComposioConnection` viejo.
