"""Defensa determinista contra monedas inventadas por el modelo.

Origen (2026-09-04): el usuario escribió "Recibí 6M de ingresos de frostbyte"
por WhatsApp y el ingreso quedó registrado como **6.000.000 USD** aunque su
moneda por defecto es COP. Los prompts ya decían "NUNCA infieras la moneda",
pero el supervisor delega en lenguaje natural ("registra un ingreso de
6.000.000 USD…") y el subagente obedece esa instrucción al pie de la letra:
basta con que UN eslabón alucine la moneda para que llegue explícita a la tool
de creación, que entonces ya no aplica el fallback a la moneda del usuario.

La regla del producto es simple y no admite matices: si el usuario no dijo la
moneda, se usa la suya por defecto. Por eso aquí no se pide nada al modelo —
se contrasta la moneda que trae la llamada contra el texto real de la
conversación. Si nadie la nombró, se descarta y la tool cae a la moneda por
defecto del usuario.

El guard es PERMISIVO a propósito: ante la duda (la moneda aparece mencionada
en el turno, aunque sea de pasada) se respeta lo que pidió el modelo. Solo
corrige el caso inequívoco: nadie escribió esa moneda en ninguna parte.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)


# Alias en español (y símbolo) por código ISO. El código ISO siempre cuenta
# como mención, incluso para monedas que no estén en este mapa.
#
# Deliberadamente NO se incluyen palabras ambiguas como "pesos" o "$" a secas:
# no identifican una moneda (COP/MXN/ARS/CLP/UYU comparten "pesos"), que es
# justo la ambigüedad que hace falta resolver con la moneda por defecto.
_CURRENCY_ALIASES: dict[str, tuple[str, ...]] = {
    "USD": ("dolar", "dolares", "usd", "us$", "u$s", "usd$", "dls"),
    "EUR": ("euro", "euros", "€"),
    "COP": ("peso colombiano", "pesos colombianos", "cop$"),
    "MXN": ("peso mexicano", "pesos mexicanos", "mxp"),
    "ARS": ("peso argentino", "pesos argentinos"),
    "CLP": ("peso chileno", "pesos chilenos"),
    "UYU": ("peso uruguayo", "pesos uruguayos"),
    "DOP": ("peso dominicano", "pesos dominicanos"),
    "CUP": ("peso cubano", "pesos cubanos"),
    "PEN": ("sol", "soles", "sol peruano", "soles peruanos"),
    "BRL": ("real", "reales", "reais", "real brasileno", "reales brasilenos"),
    "GBP": ("libra", "libras", "libra esterlina", "£"),
    "VES": ("bolivar", "bolivares"),
    "PYG": ("guarani", "guaranies"),
    "BOB": ("boliviano", "bolivianos"),
    "CRC": ("colon", "colones"),
    "GTQ": ("quetzal", "quetzales"),
    "JPY": ("yen", "yenes", "¥"),
    "CAD": ("dolar canadiense", "dolares canadienses"),
    "AUD": ("dolar australiano", "dolares australianos"),
    "CHF": ("franco suizo", "francos suizos"),
}


def _normalize(text: str) -> str:
    """Minúsculas y sin tildes, para comparar 'Dólares' con 'dolares'."""
    lowered = (text or "").lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _mentions_token(normalized_text: str, token: str) -> bool:
    """¿Aparece ``token`` como palabra (o como símbolo) en el texto?"""
    normalized_token = _normalize(token)
    if not normalized_token:
        return False
    if normalized_token.isalnum():
        # Palabra completa: "usd" no debe hacer match dentro de "usdt".
        pattern = rf"(?<![0-9a-z]){re.escape(normalized_token)}(?![0-9a-z])"
    else:
        # Símbolos y alias con signos ("us$", "€"): búsqueda literal.
        pattern = re.escape(normalized_token)
    return re.search(pattern, normalized_text) is not None


def mentions_currency(text: str, code: str) -> bool:
    """¿El texto nombra explícitamente la moneda ``code``?

    Cuenta el código ISO (USD, COP…) y los alias en español de la moneda
    ("dólares", "euros", "pesos colombianos"…). No cuentan las palabras
    ambiguas: "pesos" a secas o el símbolo "$" no identifican una moneda.
    """
    if not text or not code:
        return False
    normalized_text = _normalize(text)
    upper_code = code.strip().upper()
    tokens = (upper_code, *_CURRENCY_ALIASES.get(upper_code, ()))
    return any(_mentions_token(normalized_text, token) for token in tokens)


def resolve_currency(
    requested: str | None,
    user_default: str,
    context_texts: Sequence[str] = (),
) -> str:
    """Moneda que debe usarse en una operación pedida por el agente.

    Devuelve cadena vacía cuando hay que caer a la moneda por defecto del
    usuario: las tools ``create_expense`` / ``create_income`` ya interpretan
    una moneda vacía como "usa la del usuario" y avisan de ello en su
    confirmación.

    - Sin moneda pedida → "" (la del usuario).
    - Moneda pedida == la del usuario → se respeta.
    - Moneda pedida mencionada en la conversación → se respeta.
    - Moneda pedida que nadie nombró → "" (la del usuario), con log de aviso.
    """
    code = (requested or "").strip().upper()
    if not code:
        return ""

    default_code = (user_default or "").strip().upper()
    if code == default_code:
        return code

    if any(mentions_currency(text, code) for text in context_texts):
        return code

    logger.warning(
        "currency_guard: se descartó la moneda '%s' (nadie la mencionó); "
        "se usa la moneda por defecto del usuario '%s'",
        code,
        default_code or "(sin default)",
    )
    return ""


def mentioned_currency(
    requested: str | None,
    context_texts: Sequence[str] = (),
) -> str:
    """Moneda a aplicar en una EDICIÓN: solo si alguien la nombró.

    Editar no es crear: si el usuario pide "cambia ese gasto a 90 mil" no está
    pidiendo cambiar de moneda, así que ni la moneda inventada por el modelo ni
    la moneda por defecto deben pisar la del registro. Devuelve "" (dejar la
    que ya tiene) salvo que la conversación mencione una moneda explícita.
    """
    code = (requested or "").strip().upper()
    if not code:
        return ""
    if any(mentions_currency(text, code) for text in context_texts):
        return code
    logger.warning(
        "currency_guard: se descartó la moneda '%s' en una edición "
        "(nadie la mencionó); se conserva la del registro",
        code,
    )
    return ""


def conversation_texts(
    user_message: str | None,
    history: Iterable[Any] | None = None,
    limit: int = 6,
) -> list[str]:
    """Textos donde buscar la mención de moneda: el turno actual y los últimos
    mensajes del hilo.

    Se incluyen también los mensajes del asistente: si Tresqu preguntó
    "¿lo registro como 100 USD?" y el usuario respondió "sí", la moneda sí
    estaba sobre la mesa y no debe corregirse.
    """
    texts: list[str] = []
    for message in list(history or [])[-limit:]:
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content)
    if user_message:
        texts.append(user_message)
    return texts
