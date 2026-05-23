# Wallbit Integration — Plan ajustado

**Status:** Listo para implementación
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

---

## 8. Endpoints REST (sin cambios vs brief)

```
POST   /api/wallbit/connect              { api_key } → 200 | 400
POST   /api/wallbit/disconnect           → 200
GET    /api/wallbit/status               → { connected, last_sync_at, balance_summary }
POST   /api/wallbit/sync                 → trigger sync manual (Celery task)
GET    /api/wallbit/transactions         → mirror local paginated
GET    /api/wallbit/balance              → cached + fresh si stale
POST   /api/wallbit/statements/upload    multipart → parsed_statement_id
GET    /api/wallbit/statements/{id}      → status del parse
GET    /api/wallbit/agent/decisions      → log paginado
POST   /api/wallbit/agent/confirm/{id}   → confirma decisión pendiente
POST   /api/wallbit/limits               → set AgentLimits
GET    /api/wallbit/limits               → get AgentLimits
GET    /api/wallbit/investments          → list Investment del user
```

---

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
- `AgentDecision` se persiste **siempre**, ejecutada o no
- `WallbitAccount.kill_switch_until` desactiva toda escritura globalmente
- No commitear keys/secrets — todo en `.env`
- No `--amend` a commits pusheados, no `--no-verify`, no reset destructivo
- Logs **nunca** contienen la API key (filtrar en `client.py`)

---

## 13. Referencias

- **Brief original (técnica detallada API Wallbit, endpoints, scopes, gotchas):** `/mnt/d/Projects/challenge-wallbit/TRESQU_PILOT_BRIEF.md`
- **Wallbit docs:** https://developer.wallbit.io/docs/api-reference/introduction
- **Wallbit llms.txt:** https://developer.wallbit.io/llms.txt
- **CLAUDE.md del proyecto:** `/home/jjat00/projects/cashbot/CLAUDE.md`
