"""Guardrail de tema: evita ejecutar el supervisor con mensajes ajenos a las finanzas.

Tresqu contestaba a TODO lo que le llegara: gente que se equivoca de chat,
cadenas reenviadas y, en un caso real, otro sistema automático que respondió a
Tresqu y dejó a los dos escribiéndose entre ellos. Cada uno de esos turnos
ejecuta el supervisor completo (prompt largo + subagentes), que es donde está
el gasto de tokens.

Capas, de la más barata a la más cara:

1. ``check_flood`` — local, sin red ni LLM: ráfaga y eco. Es la ÚNICA capa que
   silencia a ciegas, porque una ráfaga es la firma de un bucle entre sistemas
   automáticos y ahí lo que hay que cortar es el bucle, no el contenido. Su
   mute dura ``MUTE_SECONDS``.
2. Atajos locales (regex): comandos, montos y vocabulario financiero pasan
   directo; también las respuestas cortas que continúan un turno reciente de
   Tresqu ("sí", "el segundo", "1000").
3. Clasificador ``gpt-4.1-nano``: on-topic / off-topic / automático. Cuesta una
   fracción ínfima de un turno del supervisor.

El silencio NO es ceguera: tras un mensaje fuera de tema los siguientes se
siguen evaluando con las capas 1-3, así que si el remitente pasa a hablar de
gastos, ingresos o inversiones, se le responde de inmediato y la racha se
reinicia. Solo el mute por ráfaga descarta sin mirar, y es temporal.

Política ante fallos: fail-open. Si el clasificador o la caché fallan, el
mensaje pasa — el guardrail nunca deja mudo a Tresqu por un error propio.

El estado (racha, ráfaga, eco, mute) vive en la caché ``default`` con TTL
explícito, igual que los candados del webhook de WhatsApp.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from telegrambot.config import OPENAI_REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


# --- Configuración (todo override-able por settings/env) ---------------------

def _conf(name: str, default):
    return getattr(settings, name, default)


# Interruptor general: apagarlo devuelve el comportamiento anterior (responder
# siempre) sin tocar código.
def _enabled() -> bool:
    return bool(_conf("TOPIC_GUARD_ENABLED", True))


# Ráfaga: más de MAX mensajes en WINDOW segundos ⇒ mute ciego de MUTE_SECONDS.
BURST_MAX = int(_conf("TOPIC_GUARD_BURST_MAX", 15))
BURST_WINDOW = int(_conf("TOPIC_GUARD_BURST_WINDOW", 60))
# Eco: el mismo texto repetido tantas veces seguidas. Se le da más margen que a
# la ráfaga y un mute más corto, porque un usuario real que insiste ("hola?",
# "hola?") repite texto sin ser un bucle.
ECHO_MAX = int(_conf("TOPIC_GUARD_ECHO_MAX", 4))
MUTE_SECONDS = int(_conf("TOPIC_GUARD_MUTE_SECONDS", 15 * 60))
ECHO_MUTE_SECONDS = int(_conf("TOPIC_GUARD_ECHO_MUTE_SECONDS", 5 * 60))
# Cuánto se recuerda la racha de mensajes fuera de tema. Pasado ese tiempo sin
# reincidir, el remitente vuelve a tener derecho al aviso.
STRIKE_TTL = int(_conf("TOPIC_GUARD_STRIKE_TTL", 24 * 60 * 60))
# Longitud máxima que se manda al clasificador (un mensaje larguísimo ya se
# clasifica bien con su arranque).
MAX_CLASSIFY_CHARS = 600

# Aviso fijo del primer mensaje fuera de tema: plantilla, cero tokens de LLM.
OFF_TOPIC_REPLY = (
    "Solo puedo ayudarte con tus finanzas personales: gastos, ingresos, ahorro "
    "e inversiones. Si te equivocaste de chat, no pasa nada — cuando quieras "
    "registrar o consultar algo de tu plata, escríbeme. 💸"
)


# --- Claves de estado --------------------------------------------------------

def scope_key(channel: str, user_id) -> str:
    """Ámbito del guardrail: un remitente en un canal."""
    return f"{(channel or 'web').lower()}:{user_id}"


def _k(kind: str, scope: str) -> str:
    return f"guard:{kind}:{scope}"


def _cache_get(key: str, default=None):
    try:
        value = cache.get(key)
        return default if value is None else value
    except Exception as exc:  # noqa: BLE001 — la caché nunca rompe el flujo
        logger.warning("topic guard: cache get falló (%s); se ignora el estado", exc)
        return default


def _cache_set(key: str, value, ttl: int) -> None:
    try:
        cache.set(key, value, ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("topic guard: cache set falló (%s); se ignora", exc)


def _cache_delete(key: str) -> None:
    try:
        cache.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("topic guard: cache delete falló (%s); se ignora", exc)


# La caché ``default`` es DatabaseCache: tocarla es I/O de base de datos, que
# Django prohíbe hacer en sincrónico dentro de código async. Los caminos async
# usan estos envoltorios.
_acache_get = sync_to_async(_cache_get)
_acache_set = sync_to_async(_cache_set)
_acache_delete = sync_to_async(_cache_delete)


def _bump(key: str, ttl: int) -> int:
    """Incrementa un contador con ventana fija. Devuelve 0 si la caché falla."""
    try:
        if cache.add(key, 1, ttl):
            return 1
        try:
            return cache.incr(key)
        except ValueError:
            # Expiró entre el add y el incr: arrancamos ventana nueva.
            cache.set(key, 1, ttl)
            return 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("topic guard: contador %s falló (%s)", key, exc)
        return 0


# --- Veredicto ---------------------------------------------------------------

@dataclass(frozen=True)
class GuardVerdict:
    """Resultado de evaluar un mensaje entrante.

    - ``allow``   → sigue el flujo normal (supervisor).
    - ``reply``   → no se ejecuta el supervisor, pero se envía este texto fijo.
    - ninguno     → silencio absoluto (``silent``).
    """

    allow: bool
    reason: str
    reply: str | None = None

    @property
    def silent(self) -> bool:
        return not self.allow and not self.reply


_ALLOW = GuardVerdict(allow=True, reason="on_topic")


# --- Capa 1: ráfaga y eco (sin red) -----------------------------------------

def check_flood(scope: str, text: str) -> GuardVerdict | None:
    """Corta bucles automáticos. Devuelve ``None`` si no hay nada que cortar.

    Es la única capa que descarta sin leer el contenido (y la única que aplica
    mute), porque un intercambio a ritmo de máquina hay que pararlo aunque los
    mensajes parezcan válidos.
    """

    if not _enabled():
        return None

    mute_key = _k("mute", scope)
    if _cache_get(mute_key):
        logger.info("topic guard: mensaje descartado, %s está en mute", scope)
        return GuardVerdict(allow=False, reason="muted")

    count = _bump(_k("burst", scope), BURST_WINDOW)
    if count > BURST_MAX:
        _cache_set(mute_key, True, MUTE_SECONDS)
        logger.warning(
            "topic guard: ráfaga de %s mensajes en %ss desde %s; mute %ss",
            count, BURST_WINDOW, scope, MUTE_SECONDS,
        )
        return GuardVerdict(allow=False, reason="flood")

    digest = hashlib.sha1(" ".join((text or "").lower().split()).encode()).hexdigest()[:16]
    echo_key = _k("echo", scope)
    previous = _cache_get(echo_key) or {}
    repeats = previous.get("n", 0) + 1 if previous.get("h") == digest else 1
    _cache_set(echo_key, {"h": digest, "n": repeats}, BURST_WINDOW * 10)
    if repeats >= ECHO_MAX:
        _cache_set(mute_key, True, ECHO_MUTE_SECONDS)
        logger.warning(
            "topic guard: %s repitió el mismo texto %s veces; mute %ss",
            scope, repeats, ECHO_MUTE_SECONDS,
        )
        return GuardVerdict(allow=False, reason="echo")

    return None


# Los canales async (agentes, chat web) no pueden tocar la caché de base de
# datos en sincrónico.
check_flood_async = sync_to_async(check_flood)


# --- Capa 2: atajos locales --------------------------------------------------

# Vocabulario que basta para dar por bueno el mensaje sin gastar clasificador.
_FINANCE_HINTS = re.compile(
    r"gast|ingres|sueld|salari|ahorr|invers|inviert|invertir|accion|acción|etf|"
    r"wallbit|tresqu|saldo|balance|presupuest|deud|prestam|préstam|cuota|"
    r"pagu|pagué|pago|compr|vend|factur|recib|tarjet|banc|transferen|nomin|"
    r"dolar|dólar|peso|euro|plata|dinero|efectivo|cripto|bolsa|mercado|"
    r"portafoli|dividend|ticker|cotiza|precio|categor|resumen|reporte|"
    r"perfil de riesgo|cuánto|cuanto|debo|cobr|arriend|alquiler|servicios|"
    r"suscripci|mesada|beca|comisión|comision|rendimiento|ganancia|pérdida|perdida",
    re.IGNORECASE,
)

# Un número con pinta de monto (12000, 12.000, 50k, $30, 1,5 millones).
_AMOUNT_HINT = re.compile(
    r"(?:[$€]\s*\d)|(?:\d[\d.,]*\s*(?:k\b|mil\b|millon|lucas\b|usd|cop|eur|mxn|ars|clp|pen|brl))|"
    r"(?:\b\d{3,}\b)",
    re.IGNORECASE,
)

# Saludos, despedidas y agradecimientos sueltos. El clasificador los falla de
# vez en cuando ("gracias!" leído como fuera de tema), y silenciar a alguien
# por ser cortés sería el peor falso positivo posible.
_COURTESY = re.compile(
    r"^(?:hola|holi|holis|buenas|buenos d[ií]as|buenas tardes|buenas noches|hey|"
    r"qu[eé] m[aá]s|gracias|muchas gracias|mil gracias|much[ií]simas gracias|"
    r"ok|oka|okey|okay|vale|dale|listo|perfecto|genial|excelente|de una|bueno|"
    r"ya|entiendo|chao|chau|adi[oó]s|hasta luego|bye|nos vemos|"
    r"jaja+|jeje+|ja+|:\)|👍|🙏|😊|🙌|❤️)"
    r"[\s!¡.,;:👍🙏🎉😊🙌❤️😅😂]*$",
    re.IGNORECASE,
)

_MAX_CONTINUATION_WORDS = 6


def _last_assistant_text(history: list) -> str:
    """Último texto que dijo Tresqu en el historial (para dar contexto)."""
    for message in reversed(history or []):
        if message.__class__.__name__ == "AIMessage":
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                return " ".join(content.split())[:300]
    return ""


def _local_allow(text: str, history: list, strike: int) -> bool:
    """``True`` si el mensaje es claramente del dominio sin consultar al modelo."""

    stripped = (text or "").strip()
    if not stripped:
        return True
    if stripped.startswith("/"):  # comandos: /perfil, /registrar, /start…
        return True
    if _FINANCE_HINTS.search(stripped) or _AMOUNT_HINT.search(stripped):
        return True
    # Cortesías y respuestas cortas a un turno de Tresqu ("sí", "el segundo",
    # "dale"): solo se dan por buenas mientras no haya racha abierta, para que
    # un intercambio automático no se cuele a base de monosílabos. Durante el
    # silencio, únicamente un mensaje del dominio lo levanta.
    if strike:
        return False
    if _COURTESY.match(stripped):
        return True
    if len(stripped.split()) <= _MAX_CONTINUATION_WORDS and _last_assistant_text(history):
        return True
    return False


# --- Capa 3: clasificador ----------------------------------------------------

_CLASSIFIER_SYSTEM = """Eres un filtro de relevancia para Tresqu, un asistente de finanzas personales (gastos, ingresos, ahorro, deudas, inversiones en acciones y ETFs, y el uso del propio producto).

Clasifica el ÚLTIMO mensaje del usuario y responde SOLO con JSON:
{{"on_topic": true|false, "automated": true|false}}

on_topic = true cuando el mensaje:
- es un saludo, una despedida, un agradecimiento o cualquier cortesía breve, aunque no mencione dinero ("hola", "buenas", "gracias", "muchas gracias", "ok", "listo", "perfecto", "jaja");
- habla de dinero, gastos, compras, ingresos, deudas, ahorro, presupuesto, inversiones, acciones, ETFs, saldos, mercado o precios;
- pide registros, reportes, resúmenes, correcciones o cambios sobre eso;
- pregunta por Tresqu, sus funciones, su cuenta, sus planes o cómo usarlo;
- responde o continúa el último mensaje de Tresqu (confirmaciones, "sí", "el segundo", un número suelto, una fecha, una categoría).

on_topic = false para cualquier otro tema: programación, tecnología, política, salud, deportes, entretenimiento, tareas escolares, religión, cadenas reenviadas o publicidad ajena.

automated = true SOLO si el mensaje parece emitido por un sistema y no escrito por una persona: respuestas automáticas, "este es un mensaje automático", "no responda a este mensaje", menús de atención automatizada, plantillas o promociones masivas de otra empresa. Una persona preguntando por otro tema (código, deportes, política) NO es automated: es solo off_topic.

Ante la duda, on_topic = true.

Último mensaje de Tresqu (contexto, puede estar vacío): {last_assistant}"""


def _classifier() -> ChatOpenAI:
    return ChatOpenAI(
        model=_conf("AGENT_CLASSIFIER_MODEL", "gpt-4.1-nano"),
        temperature=0,
        api_key=settings.OPENAI_API_KEY,
        request_timeout=OPENAI_REQUEST_TIMEOUT,
        max_retries=1,
        model_kwargs={"response_format": {"type": "json_object"}},
    )


async def _classify(text: str, history: list) -> dict:
    """``{"on_topic": bool, "automated": bool}``. Fail-open ante cualquier error."""

    try:
        response = await _classifier().ainvoke(
            [
                SystemMessage(
                    content=_CLASSIFIER_SYSTEM.format(
                        last_assistant=_last_assistant_text(history) or "(sin contexto)"
                    )
                ),
                HumanMessage(content=(text or "")[:MAX_CLASSIFY_CHARS]),
            ]
        )
        parsed = json.loads(response.content)
        return {
            "on_topic": bool(parsed.get("on_topic", True)),
            "automated": bool(parsed.get("automated", False)),
        }
    except Exception as exc:  # noqa: BLE001 — nunca bloquear por fallo propio
        logger.warning("topic guard: clasificador falló (%s); se deja pasar", exc)
        return {"on_topic": True, "automated": False}


# --- Entrada pública ---------------------------------------------------------

async def check_relevance(scope: str, text: str, history: list) -> GuardVerdict:
    """Decide si el mensaje merece un turno del supervisor.

    Un mensaje del dominio siempre pasa y además reinicia la racha, así que un
    remitente silenciado recupera la respuesta en cuanto escribe de sus
    finanzas.
    """

    if not _enabled():
        return _ALLOW

    strike_key = _k("strike", scope)
    strike = int(await _acache_get(strike_key, 0) or 0)

    if _local_allow(text, history, strike):
        if strike:
            await _acache_delete(strike_key)
        return _ALLOW

    verdict_data = await _classify(text, history)

    if verdict_data["on_topic"] and not verdict_data["automated"]:
        if strike:
            logger.info("topic guard: %s vuelve al tema; se levanta el silencio", scope)
            await _acache_delete(strike_key)
        return _ALLOW

    # Mensaje de un sistema automático: no hay a quién explicarle nada, así que
    # se salta el aviso y se calla de una. NO se aplica mute ciego: el
    # clasificador confunde a veces a una persona preguntando otra cosa con una
    # máquina, y dejar sordo a un usuario real 15 minutos es peor que gastar
    # una llamada del modelo nano por mensaje. El silencio ya corta el bucle,
    # porque un intercambio automático necesita nuestras respuestas para
    # seguir; si el otro extremo insiste, lo para la ráfaga.
    if verdict_data["automated"]:
        await _acache_set(strike_key, strike + 1, STRIKE_TTL)
        logger.info("topic guard: mensaje de aspecto automático desde %s; silencio", scope)
        return GuardVerdict(allow=False, reason="automated")

    strike += 1
    await _acache_set(strike_key, strike, STRIKE_TTL)
    if strike == 1:
        logger.info("topic guard: primer mensaje fuera de tema de %s; aviso fijo", scope)
        return GuardVerdict(allow=False, reason="off_topic", reply=OFF_TOPIC_REPLY)

    logger.info("topic guard: mensaje fuera de tema #%s de %s; silencio", strike, scope)
    return GuardVerdict(allow=False, reason="off_topic_repeat")
