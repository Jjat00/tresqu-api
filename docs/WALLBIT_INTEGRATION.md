# Wallbit Integration — Plan ajustado

> **⚠️ Documento histórico (mayo 2026):** este fue el plan de diseño previo a la implementación. La integración ya está construida; la fuente de verdad actual es el código (`wallbit/`) y la sección Wallbit del [`README.md`](../README.md). Se conserva como registro de decisiones. La sección 8 se actualizó a los endpoints reales.

**Status:** Implementado
**Brief original:** `/mnt/d/Projects/challenge-wallbit/TRESQU_PILOT_BRIEF.md` (referencia técnica completa)
**Deadline submission:** martes 26 mayo 2026, 23:59 COL
**Live final:** viernes 29 mayo 2026, 18:00 COL
**Submission name:** `Tresqu × Wallbit`

Este documento captura las decisiones de arquitectura tomadas tras hacer review del código actual de `cashbot-api/` y `chat-finance-bot/`. **Donde este doc contradiga al brief, este doc gana** — el brief fue escrito sin auditar la base de código real.

---

## 1. Decisiones tomadas (delta vs el brief)

| Tema | Brief original | Realidad / Decisión |
|---|---|---|
| **Async** | Asumía Celery existente | **No existe.** Agregamos Redis + worker + `celery` al stack |
| **Categoría de TX Wallbit** | FK directa a `categories.Category` | **`Category` no es global**, es `UserExpenseCategory` por usuario. Creamos modelo nuevo `Investment` en `wallbit/` para clasificar trades/deposits/withdraws/chests. Solo TX de tipo `CARD_PAYMENT` cruza a `Expense` + `UserExpenseCategory` |
| **Ubicación de tools** | "Registrar en `telegrambot/tools.py`" | Las tools de Wallbit viven en **`wallbit/tools.py`** (dueño por dominio). Ambos bots las importan y concatenan en su `create_agent()` — cambio de 2 líneas por bot |
| **ChatBot web (`tresqu.com`)** | "Agente con voz en el chat widget" | **No se toca.** `src/components/ChatBot.tsx` queda intacto. Todo el flujo agente va por WhatsApp + Telegram. Killer move = jurado escribe al WhatsApp público |
| **`AgentLimits`** | Defaults $500/$1000/$200 | Mismos defaults **pero configurables desde UI** (página `/dashboard/wallbit/limits` o tab en Connections) |
| **Audit log / confirmation flow** | Asumía patrón existente | **No existe.** `AgentDecision` + middleware de confirmación se construyen desde cero |
| **Parser PDF** | Asumía deps disponibles | Faltan `pdfplumber`, `pandas`, `python-magic`. Patrón base: reusar `gmailbot/email_processor.py:37 parse_purchase_email()` |

---

## 2. Lo que el código actual SÍ tiene (no construimos de nuevo)

- ✅ `pgvector 0.4.2` instalado, ya en uso (`expenses/models.py:34`, `income/models.py:152` con `VectorField(1536)` y `text-embedding-3-small`)
- ✅ RAG con precedente: `Expense.find_similar()` (`telegrambot/tools.py:767-791`) e `Income.find_similar()` (`tools.py:1257-1289`)
- ✅ Pipeline LangChain con `create_agent()` en `telegrambot/services.py:605` y `whatsappbot/services.py:749`
- ✅ Tools registry compartido (WhatsApp importa desde `telegrambot.tools` en `whatsappbot/services.py:19-48`)
- ✅ WhatsApp con **Meta Cloud API** (no Twilio), webhook `meta_webhook()`, descarga de imágenes, transcripción Whisper, vision GPT-4o (`extract_expenses_from_image`)
- ✅ Gmail parser reutilizable como template (`gmailbot/email_processor.py:37`)
- ✅ JWT `djangorestframework-simplejwt 5.5.1` + `CustomJWTAuthentication`
- ✅ Frontend stack: React 19.2, TS 5.9, Vite 6.4, Tailwind 4.1, Zustand 5 con persist, TanStack Query 5.90, shadcn (40+ componentes)
- ✅ `IntegrationsTab.tsx` en dashboard — lugar natural para sección Wallbit
- ✅ `cryptography 44.0.0` y `httpx 0.28.1` ya en `requirements.txt`

---

## 3. Stack adjustments (sábado primera tarea)

### `docker-compose.dev.yml`
Sumar:
- Service `redis` (imagen oficial, port interno 6379)
- Service `worker` (mismo build que `web` pero command = `celery -A cashbotapp worker -l info`)
- `web` y `worker` con `depends_on: [redis, db]`

### `requirements.txt`
Añadir:
```
celery==5.x
redis==5.x
pdfplumber
pandas
python-magic
```
(`cryptography`, `httpx`, `langchain`, `langchain-openai`, `pgvector` ya están — no tocar)

### `cashbotapp/celery.py` (nuevo)
- `Celery('cashbotapp')` con `broker=redis://redis:6379/0`
- `autodiscover_tasks()` para que recoja `wallbit/tasks.py`
- Wire en `cashbotapp/__init__.py` (`from .celery import app as celery_app`)

---

## 4. App `wallbit/` — estructura final

```
cashbot-api/wallbit/
├── __init__.py
├── apps.py
├── models.py         # ver sección 5
├── crypto.py         # Fernet helpers (derivar de SECRET_KEY con sha256)
├── client.py         # httpx + backoff + parser X-RateLimit-*
├── tools.py          # 10 @tool defs + export WALLBIT_TOOLS
├── parsers.py        # PDF Nequi + Bancolombia
├── memory.py         # tresqu_query_history sobre pgvector
├── tasks.py          # Celery: sync_transactions, parse_statement
├── views.py          # endpoints REST internos
├── serializers.py
├── urls.py
├── admin.py
└── migrations/
```

---

## 5. Modelos definitivos

Del brief mantenemos: `WallbitAccount`, `WallbitTxMirror`, `WallbitChestLink`, `AgentDecision`, `AgentLimits`, `ParsedStatement` (ver brief sección 4 para schema completo).

**Nuevo modelo** (no estaba en el brief):

```python
class Investment(models.Model):
    """Posición o movimiento de inversión vinculado a Wallbit.
    Separado de Expense/Income porque las TX de inversión son de naturaleza distinta
    al gasto/ingreso del día a día. Solo wallbit_tx.tx_type == 'CARD_PAYMENT' cruza
    al modelo Expense (gasto real con tarjeta Wallbit)."""
    user = FK(User)
    kind = CharField(choices=['STOCK', 'ETF', 'BOND', 'ROBO', 'CHEST'])
    action = CharField(choices=['BUY', 'SELL', 'DEPOSIT', 'WITHDRAW'])
    symbol = CharField(blank=True)            # 'TSLA', 'VOO' — null si es Chest/Robo
    chest_category = CharField(blank=True)    # 'EMERGENCIES', 'VACATIONS'... para Chests
    amount_usd = Decimal
    shares = Decimal(null=True)
    wallbit_tx = OneToOne(WallbitTxMirror, null=True, related_name='investment')
    created_at = DateTime
```

**Mapping `WallbitTxMirror.tx_type` → modelo destino:**

| `tx_type` Wallbit | Crea | Notas |
|---|---|---|
| `TRADE` | `Investment(kind=STOCK/ETF/BOND, action=BUY/SELL)` | Inferir `kind` desde `/assets/{symbol}` |
| `INTERNAL` | Solo `WallbitTxMirror` | Movimiento DEFAULT↔INVESTMENT |
| `ROBOADVISOR_DEPOSIT/WITHDRAW` | `Investment(kind=ROBO o CHEST, action=DEPOSIT/WITHDRAW)` | Si linked a `SavingsGoal` vía `WallbitChestLink`, también actualiza progreso |
| `DEPOSIT` / `WITHDRAW` externos | Solo `WallbitTxMirror` | Flujo de fondos |
| `CARD_PAYMENT` | `Expense` + `UserExpenseCategory` + `WallbitTxMirror` | **El único que cruza al modelo Expense existente** |

---

## 6. Tools registry pattern

**Las 10 tools viven en `wallbit/tools.py`** (no en `telegrambot/tools.py`):

```python
# wallbit/tools.py
from langchain_core.tools import tool

@tool
def wallbit_get_balance(...): ...
# ... 9 más

WALLBIT_TOOLS = [
    wallbit_get_balance, wallbit_list_transactions,
    wallbit_search_assets, wallbit_get_asset,
    wallbit_place_trade, wallbit_move_funds,
    wallbit_deposit_chest, wallbit_withdraw_chest,
    wallbit_set_card_status, tresqu_query_history,
]
```

**Cada bot solo agrega 2 líneas:**

```python
# telegrambot/services.py y whatsappbot/services.py
from wallbit.tools import WALLBIT_TOOLS
# ...
agent = create_agent(model, tools=EXISTING_TOOLS + WALLBIT_TOOLS, ...)
```

**Ventaja:** si en el futuro matamos Wallbit, borrar `wallbit/` deja `telegrambot/` y `whatsappbot/` limpios.

---

## 7. Confirmación de escrituras

Para las 5 tools que escriben (`place_trade`, `move_funds`, `deposit_chest`, `withdraw_chest`, `set_card_status`):

1. LLM llama la tool con args
2. Tool valida contra `AgentLimits` del user
3. Tool **NO ejecuta** — devuelve preview estructurado + persiste `AgentDecision(requires_confirmation=True)`
4. Bot muestra preview + 2 botones inline (WhatsApp: interactive buttons / Telegram: inline keyboard)
5. User tap "Confirmar" → callback handler ejecuta la tool con `confirmed=True` usando el `AgentDecision.id` como referencia
6. Tool ejecuta contra Wallbit API, actualiza `AgentDecision(executed=True, wallbit_tx_uuid=...)`

### 7.1 Escrituras: un solo intento, estado incierto y conciliación (desde 2026-09-02)

Incidente: `POST /trades` tardó más de 15 s (Wallbit responde después del fill), el cliente lo reintentó 4 veces y una compra de 20 USD se ejecutó cuatro veces. Reglas que lo hacen imposible ahora:

- **`WallbitClient` solo reintenta métodos idempotentes** (GET). POST/PATCH/DELETE tienen exactamente un intento, con timeout de lectura de 60 s. Un timeout o un 5xx en una escritura lanza `WallbitUncertainError`: *pudo* haberse aplicado.
- **Una confirmación = un POST.** `execute_place_trade` ya no reintenta como LIMIT ni hace ningún segundo envío. Las órdenes LIMIT se dimensionan en `shares` (Wallbit lo exige), redondeadas hacia abajo.
- **`AgentDecision.status`** manda el ciclo: `pending → executing → executed | failed | cancelled | uncertain`. Solo `pending` es confirmable y la transición a `executing` se toma con `SELECT … FOR UPDATE` (`claim_pending_decision`), así que una reentrega de webhook o un doble toque nunca ejecutan dos veces. Una decisión fallida **no** vuelve a ser confirmable.
- **`uncertain` se concilia, no se reenvía.** `reconcile_uncertain_decision` sincroniza el mirror y busca la transacción (símbolo, monto ±2 %, ventana de 3 min, no vinculada a otra decisión); la enlaza como `executed` o, tras 4 intentos, marca `failed`, y avisa al usuario por su canal (`wallbit/notify.py`). El chat responde «verificando», nunca «rechazada», mientras dura.
- El sync del mirror se dispara tras **cada** intento contra Wallbit, no solo tras un éxito, y `get_holdings`/`get_summary` cargan a break-even las acciones vivas que los trades sincronizados aún no explican (`cost_pending`), para no inventar ganancias entre el fill y el sync.

---

## 8. Endpoints REST (actualizado a lo implementado — fuente: `wallbit/urls.py`)

```
POST   /api/wallbit/connect/                  { api_key } → 200 | 400
POST   /api/wallbit/disconnect/               → revoca + kill switch
POST   /api/wallbit/pause/                    → pausa el agente (1h–1 semana)
POST   /api/wallbit/resume/                   → reanuda el agente
GET    /api/wallbit/status/                   → { connected, last_sync_at, ... }
POST   /api/wallbit/sync/                     → trigger sync manual (Celery task)
GET    /api/wallbit/agent/decisions/          → log paginado
POST   /api/wallbit/agent/confirm/{id}/       → confirma decisión pendiente
POST   /api/wallbit/agent/cancel/{id}/        → cancela decisión pendiente
GET/POST /api/wallbit/limits/                 → AgentLimits
GET    /api/wallbit/assets/search/            → catálogo Wallbit (acciones/ETFs)
GET    /api/wallbit/investments/              → list Investment del user
GET    /api/wallbit/portfolio/summary/        → resumen del portafolio
GET    /api/wallbit/portfolio/holdings/       → posiciones actuales
GET    /api/wallbit/portfolio/timeline/       → valor del portafolio en el tiempo
GET    /api/wallbit/portfolio/pnl-timeline/   → P&L reconstruido (txns × marketdata)
```

Del plan original NO se implementaron: `GET /transactions`, `GET /balance`
(se consultan vía agente/status), ni `POST /statements/upload` y
`GET /statements/{id}` (el parser de extractos PDF quedó fuera del alcance).

---

### 8.1 Snapshot en vivo compartido (`wallbit/portfolio.py`, desde 2026-08-27)

Wallbit está detrás de **Cloudflare rate limiting**. Una sola carga del dashboard
disparaba ~12 peticiones en ráfaga (summary + holdings + pnl-timeline, cada una con
`/balance/checking` + `/balance/stocks` + `/assets/{symbol}` por activo) y el
bloqueo llegaba como `429 "You are being rate-limited by the website owner's
configuration"`. Peor: el cliente dormía `Retry-After` (cap 8 s) × 3 reintentos,
así que cada endpoint tardaba 24–48 s y los reintentos mantenían el bloqueo abierto.
Resultado: inversiones en blanco / en $0 en el dashboard.

Arreglo: **todas** las lecturas en vivo pasan por `get_live_snapshot(account)`:

- Un `LiveSnapshot` por cuenta (`checking`, `positions`, `assets`, `fetched_at`)
  en la caché Redis compartida (`caches["marketdata"]`, fallback a `default`),
  TTL 60 s = intervalo de refetch del frontend. Summary, holdings, el ancla del
  P&L y las tools del agente (`wallbit_get_balance`, `wallbit_get_portfolio`)
  leen el mismo snapshot → **una** ronda upstream por minuto, entre workers.
- **Single-flight**: el primero toma un lock (`cache.add`) y trae los datos; los
  concurrentes esperan el resultado (hasta 20 s) en vez de repetir la llamada.
- **Fail-fast + last-good**: el cliente se construye con
  `retry_rate_limited=False` (un 429 lanza `WallbitRateLimitError` al instante,
  con `retry_after`). Si Wallbit falla se sirve la última copia buena (TTL 24 h)
  marcada `stale=True` + `as_of`; el frontend lo muestra como aviso ámbar.
- **Cooldown** tras un 429: `Retry-After` (o 90 s, tope 600) sin tocar Wallbit,
  para que la ventana de Cloudflare se libere de verdad.
- Sin nada que servir (primera carga y Wallbit caído) se lanza
  `WallbitUnavailableError` → `503 {"unavailable": true}`; nunca un portafolio
  en $0. Las tools del agente deben decir "no disponible" y, con `stale=true`,
  aclarar que son los últimos datos conocidos.
- `WallbitConnectView` llama `invalidate_snapshot(account.id)` al (re)conectar.

Smoke test: `python -m wallbit.tests.test_portfolio_snapshot_smoke`.

## 9. Frontend (`chat-finance-bot/`)

**Páginas a crear:**
- `src/pages/Connections.tsx` — card Wallbit (paste API key, status, drag-drop extractos con `react-dropzone`)
- `src/pages/WallbitLimits.tsx` (o tab dentro de Connections) — CRUD de `AgentLimits`

**Servicio nuevo:**
- `src/services/wallbit.ts` siguiendo el patrón class-based de `services/expenses/`, `services/incomes/`

**Dashboard enrichment:**
- Extender `IntegrationsTab.tsx` con sección Wallbit (balance, posiciones, últimas TX)
- **No** crear sección de Inversiones nueva en el primer entregable — usar `IntegrationsTab` como home de Wallbit

**Tipos:**
- `src/types/wallbit.ts` — `WallbitAccount`, `Investment`, `AgentDecision`, `AgentLimits`

**No tocar:**
- `src/components/ChatBot.tsx` — queda simulado, no se expone como interfaz oficial

**Routing:**
- Patrón actual: check `isAuthenticated()` en `useEffect` + navigate a `/login`. Replicar en `Connections.tsx`

**Dep nueva:**
- `react-dropzone` para upload de extractos

---

## 10. Plan día por día

### Sábado 24 — Backend foundation
Ver TaskList de la sesión actual (10 tareas, hito = "saldo" por WhatsApp).

### Domingo 25 — Escritura + RAG
- 5 tools de escritura con flujo de confirmación (`AgentDecision` + callbacks)
- Validación `AgentLimits` previa a cada escritura
- `tresqu_query_history` (RAG sobre `Expense.embedding` + `Income.embedding` existentes)
- Celery beat: sync `/transactions` cada N minutos
- **Hito:** demo 1 (recomendación contextual) funciona end-to-end por WhatsApp

### Lunes 26 mañana — Frontend + parser
- `Connections.tsx` + `WallbitLimits.tsx`
- `parsers.py` PDF Nequi + Bancolombia (reusando patrón de `gmailbot/email_processor.py`)
- Endpoint upload + Celery task de parse + ingest a `WallbitTxMirror` con embedding
- Extender `IntegrationsTab.tsx` con datos Wallbit
- **Hito:** desde `tresqu.com/connections` conectas Wallbit y subes un PDF

### Lunes 26 tarde — Polish, video, submission
- Mensajes con emojis/formato/decimales en bots
- Manejo de errores user-friendly (401/412/422 → texto humano)
- Stress test 3 demos con cuenta real
- Video 60-90 s con OBS
- Landing `tresqu.com/wallbit`
- Submission form + posts X/LinkedIn/Discord
- **Deadline:** 23:59 COL

### Martes 27 – Jueves 29 — Repercusión
- Soporte a usuarios que prueben el bot
- Capturar screenshots / testimonios
- Ensayar guion del live

### Viernes 29 18:00 — Live
- 3 demos + killer move (jurado escribe al WhatsApp con sus propias API keys)

---

## 11. Las 3 demos del live (sin cambios vs brief Sección 5)

1. **Recomendación contextual** — "Tengo $200, ¿qué hago?" → cruza `tresqu_query_history` + `wallbit_get_balance` → propone aportes a Chests → user confirma → 2 deposits
2. **Resumen del mes con patrón detectado** — "gastas más en restaurantes los jueves desde marzo" (cruce historial Tresqu + TX Wallbit del mes)
3. **Detección de anomalía + kill-switch** — push proactivo bloquea tarjeta sospechosa con `wallbit_set_card_status`

---

## 12. Reglas operativas (no negociables)

- API key Wallbit **nunca** sale del backend. Frontend solo ve estado sincronizado
- Toda escritura vía Wallbit **requiere confirmación explícita** del user en su canal
- `AgentLimits` se evalúan **antes** de cualquier escritura (hard fail si supera límites)
- `evaluate_risk_profile_gate` corre después de `AgentLimits` en **BUYs**: lee `EffectiveProfile` y, si la tolerancia no encaja con el tamaño, agrega `risk_warning` al preview y enciende `two_step_required`. Nunca bloquea — solo agrega fricción. SELL, moves entre cuentas, robo y card status no pasan por este gate. Ver [`AGENTS_RISK_PROFILE.md § Pieza 4`](AGENTS_RISK_PROFILE.md#pieza-4--gate-de-confirmación-para-buys-en-wallbit).
- `AgentDecision` se persiste **siempre**, ejecutada o no (incluye `risk_gate` en `tools_called[0].args` cuando aplica)
- `WallbitAccount.kill_switch_until` desactiva toda escritura globalmente
- No commitear keys/secrets — todo en `.env`
- No `--amend` a commits pusheados, no `--no-verify`, no reset destructivo
- Logs **nunca** contienen la API key (filtrar en `client.py`)

---

## 13. Pendientes post-MVP (2026-05-24)

Lo que **funciona hoy en prod** quedó cubierto en las secciones 4-12. Lo que sigue son brechas detectadas al revisar el flujo end-to-end después del primer sync real:

### A. Handoff de `WallbitTxMirror` → modelos de negocio

- **`CARD_PAYMENT` → `Expense`** — Crear gasto automático en `expenses.Expense` cuando entra una tx `CARD_PAYMENT` en el sync. Necesita resolver `UserExpenseCategory` (auto-categorizar con IA o dejar `null` y pedir categoría por WhatsApp como hace `gmailbot`).
- **`TRADE` / `ROBOADVISOR_*` → `Investment`** — Hoy `Investment` sólo se llena cuando el agente ejecuta una orden desde Tresqu. Las tx originadas en la app de Wallbit nunca entran a `Investment`. Mirror las desde `tasks.py:sync_wallbit_transactions` mapeando `tx_type → Investment.kind/action`.
- **Ejecuciones del agente cruzan directo al mirror** — Cuando `executors.execute_decision()` recibe una respuesta exitosa de Wallbit con la nueva tx, insertarla en `WallbitTxMirror` **en el mismo request** en vez de esperar hasta 15 min al próximo sync.

### B. Cobertura del sync

- **Paginación retroactiva** — `sync_wallbit_transactions` sólo trae `page=1&limit=50`. Si un usuario hace >50 tx entre dos ticks (15 min) se pierden las más viejas. Implementar:
  - Cursor por `account.last_sync_at` → `params["from_date"] = last_sync_at.date()`
  - Loop de paginación mientras haya más resultados
- **Backfill on-connect** — Cuando un usuario conecta Wallbit por primera vez, encolar un sync histórico completo (todas las páginas) en lugar de esperar al beat.

### C. Notificaciones proactivas

- **"Detectamos tx nueva en Wallbit"** — Cuando el sync detecta una tx con `created=True`, mandar mensaje al canal preferido del usuario (Telegram/WhatsApp) con resumen y botón "categorizar/clasificar".
- **Anomalías** — Tx fuera de los patrones históricos (símbolo nuevo, monto >2σ del promedio) → push proactivo con opción de bloquear tarjeta o revocar key.

### D. Optimizaciones

- **No re-upsertear filas inalteradas** — Hoy el `update_or_create` reescribe 50 filas en cada tick aunque nada haya cambiado. Comparar fingerprint del `raw` antes de tocar la DB.
- **`if created:` en vez de `if obj.embedding is None`** — Sutil, pero más correcto: hoy si por algún motivo se borra el embedding de una tx vieja, el siguiente sync lo recalcula (no es estrictamente lo deseado).
- **Filtrar tx ya existentes antes del loop de embeddings** — Bulk `values_list("wallbit_uuid", flat=True)` y skip de los que ya están.

### E. UI faltante en la web

- **Panel de `AgentLimits`** — Endpoints `GET/POST /api/wallbit/limits/` listos; falta la página que los gestiona.
- **Historial de `AgentDecision`** — Endpoint `/api/wallbit/agent/decisions/` paginado listo; falta el componente con filtros (ejecutadas / rechazadas / pendientes).
- **Vista de `WallbitTxMirror`** — Tabla con búsqueda semántica vía `tresqu_query_history` para que el usuario explore su historial Wallbit desde la web (hoy sólo accesible vía chat).
- **Botones de confirmación inline en el chat web** — Hoy el flujo de confirmación sólo vive en WhatsApp/Telegram (`write_tools.py` devuelve `requires_confirmation: True` + `confirmation_id`). El widget `src/components/chatbot/` no renderiza los botones.

### F. Limpieza / housekeeping

- **Eliminar `WORKER_BOOTSTRAP=1`** del worker en Railway — variable que se usó para forzar el primer deploy, ya es harmless pero ensucia.
- **Borrar `pgvector` del env `develop`** en Railway si no se está usando (prod va contra Supabase Postgres).
- **Telegram inline keyboard** — Hoy WhatsApp tiene botones de confirmación, Telegram aún recibe el preview como texto. Cablear `InlineKeyboardMarkup` en `telegrambot/services.py` para paridad.

---

## 14. Referencias

- **Brief original (técnica detallada API Wallbit, endpoints, scopes, gotchas):** `/mnt/d/Projects/challenge-wallbit/TRESQU_PILOT_BRIEF.md`
- **Wallbit docs:** https://developer.wallbit.io/docs/api-reference/introduction
- **Wallbit llms.txt:** https://developer.wallbit.io/llms.txt
- **CLAUDE.md del proyecto:** `/home/jjat00/projects/cashbot/CLAUDE.md`
