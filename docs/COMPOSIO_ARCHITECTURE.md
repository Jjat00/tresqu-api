# Composio Migration — Backend Architecture Blueprint

Reference design for migrating `gmailbot/` from direct Google OAuth + Pub/Sub to Composio as the toolkit + trigger provider. This document is the architectural source of truth for phases 3 to 7 of `COMPOSIO_GMAIL_MIGRATION.md`.

---

## Decisiones cerradas (consolidado tras revisiones cruzadas)

Producto del `backend-architect` (diseño inicial), `architect-reviewer` (revisión independiente) y `database-architect` (revisión DB):

| # | Decisión | Resuelto |
|---|----------|----------|
| 1 | Estructura: app Django `composio_integration/` | App separada (no package). Roadmap multi-agente justifica boilerplate. |
| 2 | Subdirs `services/`/`handlers/` | **Descartado**. Estructura flat (alineado con `wallbit/` y resto del repo). |
| 3 | Path webhook | Genérica: `/api/integrations/gmail/composio-webhook/`. |
| 4 | Disconnect endpoint | Incluir en fase 4 (`POST /api/integrations/gmail/disconnect/`). |
| 5 | Observabilidad | Solo logging estructurado por ahora. **NO** `/metrics`, **NO** django-prometheus. |
| 6 | Webhook con conexión `disconnected`/`failed` | 200 + drop silencioso + log warning. Composio deja de reintentar. |
| 7 | Migration strategy | Una sola migration con TRUNCATE+guard+FK swap+drop legacy. |
| 8 | Migration safety | Forzar conexión directa (puerto 5432, no pgbouncer transaction-mode) durante migrate. |
| 9 | `unique_together` ProcessedEmail | Incluir `composio_connection_id` (unique parcial `WHERE composio_connection IS NOT NULL`). |
| 10 | Retención `ProcessedEmail status=error` | Sin política por ahora (volumen bajo). Revisar en 10k filas. |
| 11 | SLOs | p99 webhook-ack <500ms, p99 e2e <30s, error rate <0.5%. |
| 12 | Idempotencia | 2 capas (ON CONFLICT webhook + select_for_update task). **Capa 3 (hash) descartada**. |
| 13 | Connect-url race condition | `select_for_update` en `ComposioConnection` al crear. |
| 14 | GDPR / User.delete | Signal `post_delete(User)` → `client.delete_trigger` + `client.delete_account` (best-effort). |
| 15 | `connected_account_id` index | Solo `unique=True` (Postgres genera B-tree). Quitado `db_index=True` redundante. |
| 16 | Trigger activation sub-estado | `ComposioConnection.status='active'` + `trigger_id == ''` → frontend muestra "activando monitor". No nuevo estado en la máquina. |

---

## 1. Module structure

**Recomendación.** Crear una app Django nueva `composio_integration/` que aloja el cliente y todo lo agnostico al toolkit; mantener `gmailbot/` como la app que aloja la logica especifica de Gmail (parser, mapping, handler WhatsApp). `gmailbot/` consume `composio_integration/` como dependencia.

```
cashbot-api/
  composio_integration/
    __init__.py
    apps.py
    client.py                # ComposioClient wrapper (singleton via Django app config)
    exceptions.py            # ComposioTransientError, ComposioPermanentError, SignatureError
    auth.py                  # create_session, build_redirect_url, resolve_user_from_account
    triggers.py              # create_trigger, delete_trigger, enable/disable
    webhooks.py              # verify_signature, parse_payload (V1/V2/V3 dispatcher)
    state_token.py           # signed state JWT for callback correlation
    models.py                # ConnectedToolkit (generic) + abstract status mixin (futuro)
    services.py              # ToolkitConnectionService (high-level orchestration)
    urls.py                  # /api/integrations/<toolkit>/connect-url|callback|webhook
    views.py                 # Genericos, despachan por toolkit slug
    tasks.py                 # provision_trigger_async, retry_failed_connections
    tests/

  gmailbot/
    __init__.py
    apps.py
    models.py                # ComposioConnection (FK semantica especifica Gmail), ProcessedEmail
    services/
      __init__.py
      email_processor.py     # (SE MANTIENE) pipeline AI parser
      payload_mapper.py      # Composio Gmail payload -> dict normalizado interno
    handlers/
      webhook_handler.py     # registrado en composio_integration via entry point
      whatsapp_handler.py    # (SE MANTIENE)
    tasks.py                 # process_gmail_message_async
    urls.py                  # routes legacy (admin/debug) si quedan
    tests/
```

**Tradeoff principal.** Dos apps anaden friccion inicial (mas imports, dos migrations folders) pero pagan dividendos cuando entren Slack / Notion / banca: el flujo connect-url/callback/webhook/firma se reusa sin tocar `gmailbot/`.

**Por que descartado mantener todo en `gmailbot/`.** El nombre del app sugeriria que Slack vive en `gmailbot/`, lo cual es semanticamente erroneo y crearia acoplamiento. Mejor pagar la extraccion ahora que no hay datos reales.

---

## 2. Composio client boundary

**Recomendacion.** Singleton inicializado en `composio_integration/apps.py` (`ready()`), expuesto como `composio_integration.client.get_client() -> ComposioClient`. `ComposioClient` es un wrapper Tresqu que envuelve `composio.Composio(api_key=settings.COMPOSIO_API_KEY)` y expone metodos de alto nivel:

- `create_connect_session(user_id: str, toolkit: str, callback_url: str) -> ConnectRequest`
- `get_connected_account(account_id: str) -> ConnectedAccountDTO`
- `create_trigger(user_id: str, slug: str, config: dict) -> TriggerDTO`
- `delete_trigger(trigger_id: str) -> None`
- `verify_webhook(headers: dict, body: bytes) -> ParsedPayload`

Errores se traducen a una jerarquia propia (`ComposioTransientError` vs `ComposioPermanentError`). Retries solo para transient: backoff exponencial con jitter (1s, 2s, 4s, max 3 intentos) usando `tenacity` o decorador propio. Permanent (4xx auth, invalid signature) no se reintentan.

**Tradeoff principal.** El wrapper anade indireccion pero (a) permite stubs en tests sin mockear el SDK, (b) aisla cambios de version del SDK (V3 hoy, V4 manana), (c) centraliza logging/metricas.

**Por que descartado factory per-request.** El SDK mantiene un HTTPX client interno; recrearlo por request multiplica overhead TCP. Singleton es thread-safe segun docs Composio.

---

## 3. ProcessedEmail FK migration

**Recomendacion.** `ProcessedEmail.user = ForeignKey(User, on_delete=CASCADE)` como fuente de verdad durable, mas `composio_connection = ForeignKey(ComposioConnection, null=True, on_delete=SET_NULL)` como referencia debil para auditoria. `unique_together = ('user', 'gmail_message_id')`.

Estrategia de migracion (sin datos reales, confirmado):

1. Migration data-destructive: `RunSQL("TRUNCATE TABLE gmailbot_processedemail RESTART IDENTITY CASCADE")`.
2. AlterField/AddField para `user` (no-null tras truncate) y `composio_connection` (nullable).
3. RemoveField `google_account`.
4. AlterUniqueTogether al nuevo tuple.
5. En la misma migration: drop `GoogleAccount` y `GmailWatch` tablas (fase 5).

**Tradeoff principal.** `user` no captura el origen del email (que cuenta Gmail si el usuario reconecta), pero ese caso es marginal (un usuario, una cuenta Gmail por ahora) y `composio_connection` nullable lo cubre en logs.

**Por que descartado FK solo a `ComposioConnection`.** Si el usuario desconecta y reconecta, queremos preservar `ProcessedEmail` historial para dedupe; un FK a `ComposioConnection` con `CASCADE` borraria el historial. Con `SET_NULL` no se pierde pero el FK durable (`user`) sigue siendo necesario para queries de dashboard.

---

## 4. Webhook handler

**Recomendacion.** Endpoint `POST /api/integrations/gmail/composio-webhook/` (publico, exempto de JWT/CSRF). Flujo:

1. **Verificacion sincrona (<50ms):** leer raw body, llamar `webhooks.verify_signature(headers, body)`. Si falla -> 401 inmediato sin log de payload.
2. **Parse y dispatch:** `parse_payload(body)` detecta version V1/V2/V3 (delegando al SDK cuando posible). Extrae `trigger_slug`, `metadata.user_id`, `data`.
3. **Idempotencia row-level:** `INSERT ... ON CONFLICT DO NOTHING` en `ProcessedEmail(user_id, gmail_message_id)` con `processing_status='pending'`. Si la fila ya existia con status `processed`, responder 200 sin encolar.
4. **Encolar Celery task:** `process_gmail_message_async.delay(processed_email_id=...)` y responder 202 Accepted en <500ms.

**Celery task shape:**

```
process_gmail_message_async(processed_email_id: int)
  -> fetch ProcessedEmail row
  -> hidratar contexto (User, ComposioConnection)
  -> email_processor.parse(payload_data)  # AI parser
  -> if expense detectado: crear Expense + whatsapp_handler.ask_category()
  -> update ProcessedEmail.processing_status
```

Retry policy: `autoretry_for=(ComposioTransientError, OpenAIRateLimitError)`, `max_retries=5`, `retry_backoff=True`, `retry_jitter=True`. Errores permanentes marcan `processing_status='error'` con `ai_response` capturando el stacktrace resumido.

**Versionado V1/V2/V3.** Usar `composio.triggers.verify_webhook` cuando expone parse; fallback a parser propio que detecta presencia de `metadata.trigger_slug` (V3) vs payload plano (V1).

**Tradeoff principal.** Acknowledge en 202 antes de procesar significa que un crash del worker no propaga al webhook (Composio cree que llego OK). Mitigacion: la fila `ProcessedEmail` ya esta persistida con `pending`; un Celery beat `reprocess_stale_pending` (>10 min en pending) recupera huerfanos.

**Por que descartado sync processing.** AI parser puede tardar 3-8s; el SLA de Composio de 5s nos sacaria de la cola y dispararia reintentos duplicados.

---

## 5. Connect flow endpoints

**Recomendacion para correlacionar callback:** **state param firmado tipo JWT corto**.

- `GET /api/integrations/gmail/connect-url/` (JWT auth):
  - Crea/actualiza `ComposioConnection(user, status=pending)`.
  - Genera `state = jwt.encode({"uid": user.id, "tk": "gmail", "exp": now+10min, "nonce": secrets.token_urlsafe(8)}, settings.SECRET_KEY)`.
  - `callback_url = f"{BASE_URL}/api/integrations/gmail/callback/?state={state}"`.
  - Llama `client.create_connect_session(...)` y devuelve `{"redirect_url": cr.redirect_url}`.

- `GET /api/integrations/gmail/callback/?state=...&status=success&connected_account_id=ca_xxx` (publico):
  - Decode + verify `state` (firma, exp, nonce no-replay via cache 10min).
  - Doble validacion: `client.get_connected_account(ca_xxx).user_id == str(state["uid"])`. Si no, 403.
  - `ComposioConnection.update(connected_account_id, google_email, status=active)`.
  - Encolar `provision_gmail_trigger_async.delay(user_id)`.
  - Redirect a `FRONTEND_URL/dashboard?gmail=connected`.

**Tradeoff principal.** JWT state requiere gestion de nonce anti-replay (cache Redis con TTL). Pero sin state no tenemos forma defendible de saber que el usuario que llego al callback es el que inicio el flujo (el `connected_account_id` solo viene en query, spoofeable).

**Por que descartado solo lookup por `connected_account_id`.** Es necesario (defensa en profundidad) pero insuficiente: un atacante con un `ca_xxx` ajeno conocido podria provocar UI poisoning en el dashboard del owner real al disparar el callback. El state firmado prueba que el callback viene de un flujo iniciado por ese usuario.

---

## 6. Trigger activation

**Recomendacion.** Async en Celery (`provision_gmail_trigger_async`), no sincrono.

- El callback responde rapido al usuario; la creacion del trigger puede tardar y/o fallar transitoriamente.
- Task: `client.create_trigger(user_id, "GMAIL_NEW_GMAIL_MESSAGE", config={})`, persistir `trigger_id`. Reintentos: 5 con backoff hasta 5 min.
- Si falla definitivamente: `ComposioConnection.status='failed'`, `last_error=...`, y notificar al usuario por WhatsApp ("no pudimos activar el monitoreo de emails, reintenta desde el dashboard").

Endpoint manual de retry: `POST /api/integrations/gmail/retry-trigger/` (JWT auth) que reencola la task.

**Tradeoff principal.** El usuario ve "conectado" en el dashboard antes de que el trigger este activo (gap de pocos segundos). Mitigacion: el `RiskProfileCard`-style widget muestra sub-estado "activando monitor" hasta que el trigger este `active`.

**Por que descartado sync.** Bloquear el callback HTTP hasta que el trigger este creado significa que un fallo transitorio en Composio rompe el flujo de onboarding. La conexion ya es valor; el trigger es un detalle que puede esperar segundos.

---

## 7. ComposioConnection state machine

```
        ┌────────────────────────────────────────────┐
        │                                            ▼
   [none] ──connect-url──▶ pending ──callback OK──▶ active ──disconnect/revoke──▶ disconnected
                              │                       │
                              │                       │
                       callback fails           trigger fails / webhook 401 repetidos
                              │                       │
                              ▼                       ▼
                           failed                  failed
                              │                       │
                              └──retry-connect───────┘
```

Transiciones validas y disparadores:

| From          | To           | Triggered by                                          |
|---------------|--------------|-------------------------------------------------------|
| none          | pending      | `GET /connect-url/`                                   |
| pending       | active       | `GET /callback/` (state + account validados)          |
| pending       | failed       | callback con `status != success` o validacion falla   |
| active        | disconnected | `POST /disconnect/` o webhook 401 persistente (>3x)   |
| active        | failed       | trigger provisioning task agota retries               |
| failed        | pending      | `POST /retry-connect/` (regenera connect-url)         |
| disconnected  | pending      | nuevo `GET /connect-url/`                             |

Cualquier transicion la realiza el servicio (`services.ToolkitConnectionService`), nunca el view. Auditoria via Django signals -> `ComposioConnectionLog` (opcional fase 2).

**Tradeoff principal.** Estados granulares anaden complejidad de UI; valen la pena porque debugear "por que no me llegan emails" sin estados es brutal.

**Por que descartado bool `is_active`.** No distingue "nunca conecto" de "conecto y se rompio", informacion clave para soporte.

---

## 8. Cross-cutting concerns

**Logging estructurado.** Logger `composio_integration` con campos obligatorios en `extra`: `connected_account_id`, `user_id`, `trigger_id` (cuando aplique), `toolkit`, `event` (uno de: `connect_url_generated`, `callback_received`, `webhook_received`, `webhook_signature_failed`, `trigger_created`, `email_processed`). JSON formatter en prod (Railway captura stdout).

**Metricas (Prometheus / RED).** Counter `composio_webhook_received_total{toolkit,status}`, Counter `composio_webhook_signature_failed_total{toolkit}`, Histogram `composio_email_parser_duration_seconds`, Counter `composio_connection_state_transitions_total{from,to}`. Exponer en `/metrics` ya existente.

**Seguridad.**
- Webhook: solo HMAC via `verify_webhook` con `COMPOSIO_WEBHOOK_SECRET`. Sin firma valida -> 401 antes de parse. Documentar que la URL es semi-publica y la firma es la unica defensa.
- Callback: state JWT firmado + cross-check `connected_account.user_id`.
- Secrets en env vars (Railway), nunca en codigo. `COMPOSIO_API_KEY` y `COMPOSIO_WEBHOOK_SECRET` solo accesibles desde `composio_integration/client.py` y `webhooks.py`.
- mTLS no aplica (HTTPS publico contra Composio cloud).

**Idempotencia.** Capa 1: dedupe en webhook handler por `(user, gmail_message_id)` antes de encolar. Capa 2: Celery task usa `select_for_update` al cargar `ProcessedEmail` para evitar doble-procesamiento si Composio reintenta antes del 202. Capa 3: si el task crea `Expense`, hashear `(user, amount, sender, date)` no es necesario porque `ProcessedEmail` ya ata el origen.

**Rate limits.** No documentados por Composio. Asumir 100 req/min por API key. Wrapper aplica circuit breaker (`pybreaker`): si 5 fallos consecutivos transient en 60s, abrir circuito 30s. Los triggers son polling 15min minimo, asi que el ingreso de webhooks no nos satura.

---

## 9. Future toolkits readiness

**Recomendacion.** El diseno de `composio_integration/` ya cumple:

- **Views genericas:** `/api/integrations/<toolkit>/connect-url/`, `/callback/`, `/composio-webhook/` despachan por `toolkit` URL param. Cada toolkit registra un `ToolkitHandler` via entry point en su `apps.py`:

  ```
  # gmailbot/apps.py
  def ready(self):
      from composio_integration.registry import register_toolkit
      from gmailbot.handlers.webhook_handler import GmailWebhookHandler
      register_toolkit("gmail", GmailWebhookHandler())
  ```

- **`ToolkitHandler` protocol:** `handle_webhook(parsed_payload, connection) -> None`, `on_connected(connection) -> None`, `triggers_to_provision() -> list[TriggerSpec]`.

- **Modelo extensible:** `ComposioConnection` puede evolucionar a `ConnectedToolkit(user, toolkit, connected_account_id, ...)` con `unique_together=('user', 'toolkit')` cuando entre el segundo toolkit. La migration es trivial (add `toolkit='gmail'` default + drop OneToOne -> FK).

- **Connect flow reutilizable:** state JWT incluye `tk` (toolkit slug); el callback rutea a `registry.get_handler(state["tk"]).on_connected(...)`.

Manana, agregar Slack es: crear `slackbot/handlers/webhook_handler.py` implementando el protocol, `register_toolkit("slack", ...)`, definir su parser. Cero cambios en `composio_integration/`.

**Tradeoff principal.** Diseno generico desde dia 1 (con un solo toolkit) anade ~150 LoC de boilerplate (registry, protocol, dispatcher) que no se justifican si manana cancelamos los otros toolkits. Asumiendo el roadmap declarado, vale la pena.

**Por que descartado "lo extraemos cuando llegue el segundo".** Cuando llegue Slack vamos a estar en deadline; el costo de refactor sera mayor que el costo de hacerlo bien ahora con el contexto fresco.

---

## Pendientes para el usuario

- `[PENDIENTE: confirmar si el endpoint webhook debe ser `/api/integrations/gmail/composio-webhook/` (generico) o mantener `/gmail/composio-webhook/` (legacy path ya documentado en migration doc)].` La recomendacion es el generico, pero implica avisar a quien este configurando el dashboard de Composio.
- `[PENDIENTE: politica de retencion de `ProcessedEmail` con `status=error`].` Hoy no se limpian; con escala importa.
- `[PENDIENTE: confirmar si queremos exponer `POST /api/integrations/gmail/disconnect/` en fase 4 o diferirlo a fase 6].` Sin disconnect, el usuario que quiera "desconectar" tiene que ir a la UI de Composio.
- `[PENDIENTE: SLO concretos].` Propuesta inicial: p99 webhook-ack < 500ms, p99 end-to-end (email recibido -> WhatsApp enviado) < 30s, error rate webhook < 0.5%. Confirmar antes de instrumentar alerting.
