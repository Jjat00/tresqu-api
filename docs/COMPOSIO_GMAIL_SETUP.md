# Setup Gmail via Composio

Guía operativa para conectar Gmail al backend de Cashbot/Tresqu después
de la migración a Composio. Esta guía cubre el setup en el dashboard de
Composio, las env vars en Railway, la aplicación de la migration y
verificación end-to-end.

Si buscas el diseño arquitectónico, ver
[COMPOSIO_ARCHITECTURE.md](./COMPOSIO_ARCHITECTURE.md). Para el plan
completo de migración por fases, ver
[COMPOSIO_GMAIL_MIGRATION.md](./COMPOSIO_GMAIL_MIGRATION.md).

## 1. Crear el Auth Config en Composio

1. Entrar a https://platform.composio.dev → workspace de Tresqu.
2. **Toolkits** → buscar "Gmail" → **Setup**.
3. Seleccionar **Composio-managed OAuth** (no requiere cuenta Google
   Cloud propia; aprovechamos el CASA aprobado de Composio — ese es
   el motivo de migrar fuera del OAuth directo).
4. Scopes: dejar los default que pide Composio para "leer correos".
5. **Save** → copiar el `auth_config_id` (formato `ac_xxx`).

## 2. Crear la Webhook Subscription

1. **Project Settings** → **Webhooks** → **Add Subscription**.
2. **Webhook URL**: `https://<tu-backend>/api/integrations/gmail/composio-webhook/`
   - En producción: la URL pública de Railway (ej.
     `https://api.tresqu.com/api/integrations/gmail/composio-webhook/`).
   - En staging: misma idea con el dominio de staging.
3. **Events**: marcar solo `composio.trigger.message`.
4. **Save** → copiar el `secret` (formato `whsec_xxx` o similar).

> El endpoint es público (sin JWT) — la única defensa es la firma
> HMAC con este secret. NO lo expongas en logs ni commits.

## 3. Env vars en Railway

En el servicio `cashbot-api` de Railway, agregar:

| Variable | Valor |
|---|---|
| `COMPOSIO_API_KEY` | API key del workspace Composio |
| `COMPOSIO_WEBHOOK_SECRET` | El secret del paso 2 |
| `COMPOSIO_GMAIL_AUTH_CONFIG_ID` | El `ac_xxx` del paso 1 |
| `FRONTEND_URL` | URL pública del frontend (`https://tresqu.com`), sin trailing slash |

El callback URL del backend que Composio usa para regresar al usuario
se deriva automáticamente del request entrante (`request.build_absolute_uri`),
así que no hay env var para "URL del backend": funciona igual en
localhost, ngrok y Railway sin configuración adicional.

## 4. Instalar dependencias

```bash
# En el venv del host (o el container Railway al rebuild)
pip install composio==0.13.1
```

`tenacity` ya estaba en `requirements.txt`. Railway autodeploy hace
`pip install -r requirements.txt` en cada deploy desde `main`.

## 5. Aplicar las migrations

**IMPORTANTE**: aplicar con **conexión directa a Postgres** (puerto
5432), NO vía el pooler de Supabase (puerto 6543, transaction-mode).
Las migrations 0004/0005 mezclan DDL en una sola transacción y
pgbouncer transaction-mode las rompe.

Una opción rápida: temporalmente apuntar `DATABASE_URL` al puerto 5432
durante el `migrate`, después revertir.

```bash
python manage.py migrate gmailbot
```

Las dos migrations relevantes:

- `0004_composio_connection_and_fk_swap` — crea `ComposioConnection`,
  TRUNCATE `ProcessedEmail` con guard, FK swap de `google_account` →
  `user` + `composio_connection`, unique constraint parcial.
- `0005_drop_legacy_google_models` — borra `GoogleAccount` y
  `GmailWatch`.

El guard del 0004 aborta si `ProcessedEmail` tiene filas. Eso es a
propósito — si fallara en producción significa que hay datos, hay que
respaldar primero.

## 6. Conectar una cuenta Gmail (test manual)

Desde el frontend autenticado como usuario de prueba:

1. Llamar `GET /api/integrations/gmail/connect-url/` (con JWT).
   - Respuesta: `{ "redirect_url": "https://connect.composio.dev/link/ln_...", "connected_account_id": "ca_..." }`.
2. Abrir el `redirect_url` en el navegador — autenticar la cuenta Gmail.
3. Composio redirige al callback. El backend valida el state JWT +
   verifica que el `connected_account_id` pertenezca al user y
   redirige al frontend con `?gmail=connected`.
4. En background, una task Celery (`provision_triggers_async`) crea
   el trigger `GMAIL_NEW_GMAIL_MESSAGE` para esa cuenta.

Verificar:

```python
# Django shell
from gmailbot.models import ComposioConnection
ComposioConnection.objects.get(user__email='tester@example.com')
# status='active', trigger_id='ti_...'
```

## 7. Smoke test E2E

1. Cuenta Gmail conectada (paso 6) y `trigger_id` poblado.
2. Mandar un correo de compra real al inbox conectado (ej. receta de
   Rappi, MercadoPago, etc.).
3. Composio detecta el correo en su próximo poll (~2 min por default; configurable vía `interval` en el trigger config).
4. POST al webhook → backend verifica firma → `ProcessedEmail` se
   inserta con `status='pending'` → Celery task se encola.
5. Celery worker procesa: AI parse → si es compra/ingreso, crea
   `Expense` o `Income`, manda WhatsApp pidiendo categoría.
6. Verificar logs del backend (Railway):
   ```
   gmail webhook enqueued  user_id=... processed_email_id=...
   ```
7. Verificar el `Expense`/`Income` en la DB y la notificación en WhatsApp.

## 8. Operativa / troubleshooting

### Reintentar provision del trigger

Si `ComposioConnection.status='failed'` y `last_error` menciona
"trigger provisioning failed":

```
POST /api/integrations/gmail/retry-trigger/
```

(JWT) — re-encola la task con backoff.

### Reprocesar emails huérfanos

El beat `composio-reprocess-stale-pending` corre cada 5 min y
re-encola `ProcessedEmail` que llevan >10 min en `pending`. Si por
algún motivo no procesó, manualmente:

```python
from gmailbot.composio_tasks import process_gmail_message_async
from gmailbot.models import ProcessedEmail
for pe in ProcessedEmail.objects.filter(processing_status='pending'):
    process_gmail_message_async.delay(pe.id)
```

### Desconectar Gmail

Desde el frontend:

```
POST /api/integrations/gmail/disconnect/
```

(JWT) — borra trigger y connected_account en Composio (best-effort),
marca la conexión local como `disconnected`. Webhook entrantes
posteriores se loguean y droppen con 200 OK.

### Borrar User (GDPR)

El signal `pre_delete(User)` revoca trigger + connected_account en
Composio antes de borrar el row. Si Composio está caído, el delete
del User sigue (best-effort).

## 9. Tradeoffs aceptados

- **Latencia ~2 min** (default del trigger; configurable bajando el
  `interval` en `composio_handler.py:trigger_specs` y reconectando):
  los triggers Gmail de Composio son polling, no push. Es el costo por
  bypass del CASA. No es negociable hasta que Composio agregue push
  (no anunciado).
- **Webhook dropped si conexión desconectada**: 200 + log + drop. Si
  el usuario reconecta y Composio reusa el mismo `connected_account_id`,
  el row vuelve a `active` por el callback. Si crea uno nuevo, el
  webhook llega al nuevo row.
- **State JWT TTL 10 min**: si el usuario tarda más de 10 min entre
  abrir el `redirect_url` y completar OAuth en Composio, el callback
  redirige a `?gmail=invalid_state` y hay que volver a empezar.

## 10. Limpieza post-migración

Tras validar E2E que todo funciona, ya están eliminados del repo
(fase 5 de la migración):

- `gmailbot/oauth.py`, `gmailbot/encryption.py`, `gmailbot/gmail_service.py`
- `gmailbot/webhook_urls.py`
- `gmailbot/management/commands/renew_gmail_watches.py`
- `gmailbot/management/commands/gmail_manual_sync.py`
- Modelos `GoogleAccount` y `GmailWatch`
- Env vars `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
  `GOOGLE_REDIRECT_URI`, `GOOGLE_PUBSUB_TOPIC`,
  `GOOGLE_CLOUD_PROJECT_ID`, `GMAIL_TOKEN_ENCRYPTION_KEY`

Borrarlas también del dashboard de Railway tras confirmar que la
migración funciona.
