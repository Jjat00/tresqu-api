# Analista de mercado (`agents/subagents/analyst.py` + `marketdata/`)

Tercer miembro del equipo de agentes de Tresqu (junto al subagente de gastos,
el de Wallbit y el perfilador de riesgo). Es **solo lectura**: ayuda al usuario
a decidir dando **datos + contexto + educación** sobre activos — nunca asesoría
específica ("compra/vende X") ni predicciones de mercado.

---

## ¿Por qué existe la capa `marketdata/`?

La API pública de Wallbit **no expone precios históricos**: `/assets/{symbol}`
solo da el precio actual + fundamentales. Para responder "¿cómo va NVDA este
mes?" y para dibujar el gráfico del dashboard hace falta una **serie temporal**,
así que la traemos de un **proveedor externo intercambiable**.

```
agents/subagents/analyst.py   ← subagente (LLM read-only)
  └─ analyst_tools.py         ← tools que conectan al resto del sistema
       ├─ get_asset_quote      → reusa wallbit.tools.wallbit_get_asset (precio + fundamentales)
       ├─ get_price_history    → marketdata.service.get_price_history
       ├─ get_user_risk_profile→ agents.effective_profile.get_effective_profile
       └─ get_user_portfolio   → wallbit.portfolio.safe_get_summary/holdings (peso por símbolo)

marketdata/
  ├─ providers.py   ← PriceProvider (Protocol) + TwelveDataProvider; get_provider() = punto de swap
  ├─ service.py     ← rangos→params, normalización canónica, caché por (símbolo, rango), fallback last-good
  ├─ views.py       ← GET /api/market/assets/{symbol}/history/
  └─ exceptions.py  ← taxonomía (Auth/NotFound/RateLimit/Config)
```

**Proveedor por defecto:** [Twelve Data](https://twelvedata.com/) (free ~800
créditos/día, 8/min). Para migrar a Finnhub u otro, basta cambiar `get_provider()`.

---

## Cómo se conecta al equipo

El supervisor (`agents/supervisor.py`) expone el analista como la tool
`analyze_investment`. Se enruta cuando el usuario pregunta por la evolución de
un precio, pide explicar un activo, o si encaja con su perfil. El analista cruza:

- **Mercado** (`get_price_history` / `get_asset_quote`)
- **Perfil de riesgo efectivo** (`get_user_risk_profile`)
- **Portafolio Wallbit** (`get_user_portfolio`, con peso % por posición)

…para enmarcar la respuesta sin recomendar operar. Si el usuario quiere comprar
o vender, eso lo maneja el subagente Wallbit (operación real con confirmación).

Es read-only: devuelve texto plano, sin `pending_container` ni flujo de
confirmación.

---

## Rangos y caché

`marketdata/service.py:RANGE_PARAMS`:

| Rango | Twelve Data (interval, outputsize) | TTL caché |
|-------|------------------------------------|-----------|
| `1d`  | 5min, 78   | 2 min |
| `1w`  | 30min, 140 | 5 min |
| `1m`  | 1day, 30   | 30 min |
| `3m`  | 1day, 90   | 30 min |
| `1y`  | 1day, 260  | 12 h |
| `5y`  | 1week, 260 | 12 h |
| `max` | 1month, 300| 12 h |

- **Costo:** 1 petición de `/time_series` (un símbolo + un rango) = **1 crédito**.
  "¿Cómo va X este mes?" = 1 crédito, cacheado 30 min. Cambiar de rango = otra
  clave de caché = 1 crédito la primera vez.
- **Caché global por `(símbolo, rango)`** en un alias Redis dedicado
  (`caches["marketdata"]`): el dashboard y el chat comparten el mismo valor
  cacheado. Las lecturas/escrituras de caché **degradan con gracia** si Redis no
  está disponible (tratan como miss / no-op; nunca 500).
- **Fallback last-good:** si el proveedor falla (cuota/transporte) se devuelve la
  última respuesta buena con `stale: true`. Si no hay, el endpoint responde 502.
- Serie vacía del proveedor → 404.

---

## Endpoint REST (dashboard)

`GET /api/market/assets/{symbol}/history/?range=1m` (JWT). No requiere cuenta
Wallbit. Respuesta canónica:

```json
{
  "symbol": "NVDA", "range": "1m",
  "points": [{ "t": "2026-05-01", "price": 123.45 }],
  "summary": { "current": 123.45, "change_abs": 4.5, "change_pct": 3.8,
               "high": 130.0, "low": 110.0, "trend": "alcista", "points_count": 30 },
  "stale": false, "source": "twelvedata"
}
```

Búsqueda de catálogo (para explorar cualquier activo invertible en Wallbit):
`GET /api/wallbit/assets/search/?q=&category=&limit=` → `{ assets: [...] }`.

---

## Variables de entorno

```
TWELVE_DATA_API_KEY=        # sin ella, el histórico/gráfico no carga; el resto sigue
TWELVE_DATA_BASE_URL=https://api.twelvedata.com
MARKETDATA_CACHE_URL=redis://localhost:6379/2   # Redis dedicado (db 2). En prod, apuntar al Redis real
AGENT_ANALYST_MODEL=gpt-4.1
```

---

## Frontend

- **Gráfico de precios** por rangos en un modal de detalle
  (`StockPriceChart.tsx`), al hacer clic en una posición o en un activo del
  catálogo.
- **"Explorar activos"** (`AssetExplorer.tsx`): tabla con tabs de categoría
  (Populares por defecto) + buscador. Se llena del catálogo de Wallbit
  (sin costo de Twelve Data); el precio en el tiempo se carga **bajo demanda**
  al abrir el detalle.

---

## Guardrail no-asesoría

El prompt del analista (`agents/subagents/analyst.py`) copia textualmente la
regla del subagente Wallbit: tips generales OK (diversificación, horizonte,
fondo de emergencia), pero nunca "comprá/vendé X" ni "va a subir/bajar". Solo
habla de lo que **ya pasó** (datos históricos) y de educación financiera.

---

## Caveats / pendientes

- El "% del período" se calcula sobre la serie del rango (p. ej. 1d ≈ cambio
  intradía desde la apertura), no estrictamente vs. cierre anterior.
- Sin pre-warm: el primer hit de un `(símbolo, rango)` en frío pega al proveedor.
- Roadmap: alertas proactivas, educador de asignación, visualización del grafo
  de agentes, gráfico como imagen en WhatsApp, wiring del chat web al supervisor.
