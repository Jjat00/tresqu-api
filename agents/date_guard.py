"""Defensa determinista contra años inventados por el modelo.

El 2026-10-01 el usuario escribió "12000 gaseosa" y el gasto quedó con fecha
2023-10-01: el modelo puso un año que nadie dijo. Igual que con la moneda
(``currency_guard``), aquí no se le pregunta nada al modelo.

La regla es deliberadamente estrecha, porque cambiar una fecha bien puesta es
tan malo como dejar una mal puesta. Solo se corrige cuando el usuario NO dio
ninguna fecha: ni un mes, ni una fecha en números, ni un año, ni una
referencia como "hace…", "el año pasado", "en 3 semanas". Sin nada de eso, lo
que dijo fue "hoy", "ayer", un día de la semana o nada, y la fecha correcta
cae en los últimos días: un año de hace dos o más años es un error seguro.
Se cambia por el año actual (o el anterior si quedaría en el futuro); si esa
fecha no existe (29 de febrero), se deja como estaba. Solo se aplica al
CREAR: al editar, la fecha viene del registro guardado.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Iterable

logger = logging.getLogger(__name__)

_MONTHS = (
    "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|"
    "noviembre|diciembre|ene|feb|mar|abr|may|jun|jul|ago|sep|sept|oct|nov|dic|"
    "january|february|march|april|june|july|august|september|october|november|"
    "december|jan|aug|dec"
)

# Cualquier señal de que el usuario dio una fecha o un período propio.
_EXPLICIT_DATE = re.compile(
    rf"\b(?:{_MONTHS})\b"
    r"|\d{1,2}\s*[/.-]\s*\d{1,2}"  # 15/03, 15-03-24, 15.03
    r"|\b(?:19|20)\d{2}\b"  # un año escrito
    r"|\b(?:del?|el|en)\s+(?:el\s+|año\s+)?'?\d{2}\b|'\d{2}\b"  # "del 24", "en el 24", "el 15", "'24"
    # Números con palabras: pueden ser un año o un día ("en el veinticuatro",
    # "dos mil veinticuatro", "el quince"). Ante la duda, no se corrige.
    r"|\b(?:dieci\w*|veint\w*|treint\w*|cuarent\w*|cincuent\w*|sesent\w*|setent\w*|"
    r"ochent\w*|novent\w*|diez|once|doce|trece|catorce|quince|mil|"
    r"twenty|thirty|nineteen|twelve|eleven|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen)\b"
    r"|\bhace\b|\batr[aá]s\b|\bantepasad|\bpasad[oa]\b|\banterior\b"
    r"|\b(?:años?|mes(?:es)?|semanas?|d[ií]as)\b"
    r"|\b(?:ago|last|years?|months?|weeks?|days)\b",
    re.IGNORECASE,
)


# Señales de que el usuario dijo EL DÍA del movimiento. Es un detector aparte
# de ``_EXPLICIT_DATE``: aquel es amplio a propósito (ante la duda no corrige un
# año) y toma "20 mil" como fecha por la palabra "mil"; aquí un falso positivo
# deja pasar una fecha copiada del historial.
_DAY_WORD = (
    r"primero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|"
    r"trece|catorce|quince|dieci\w+|veinte|veinti\w+|treinta(?:\s+y\s+uno)?"
)
_DAY_SIGNAL = re.compile(
    rf"\b(?:{_MONTHS})\b"
    r"|\d{1,2}\s*[/.-]\s*\d{1,2}"  # 15/03, 5-10
    r"|\b(?:19|20)\d{2}\b"  # un año escrito
    # "el 5", "del 15", "día 3", "el primero", "el quince"
    rf"|\b(?:el|del|al|d[ií]a)\s+(?:\d{{1,2}}(?![\d.,%])|(?:{_DAY_WORD})\b)"
    r"|\bhace\b|\batr[aá]s\b|\bantepasad|\bpasad[oa]\b|\banterior\b"
    r"|\bantes\b|\bdespu[eé]s\b|\b(?:d[ií]as?|semanas?|mes(?:es)?|años?)\b"
    r"|\b(?:ayer|anoche|antier|anteayer|anteanoche|ma[ñn]ana|lunes|martes|"
    r"mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|finde|fin\s+de\s+semana|"
    r"quincena|yesterday|tomorrow|ago|last|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|weekend)\b",
    re.IGNORECASE,
)
_MEMORY_USER_LINE = re.compile(r"^\[\d{4}-\d{2}-\d{2}\]\s+Usuario:\s*(.*)$", re.MULTILINE)


def _user_gave_a_date(texts: list[str]) -> bool:
    return any(_EXPLICIT_DATE.search(t) for t in texts)


def user_gave_a_day(texts: Iterable[str]) -> bool:
    return any(_DAY_SIGNAL.search(t or "") for t in texts)


def memory_user_texts(lines: Iterable[str]) -> list[str]:
    """Mensajes del usuario dentro de resultados de memoria, sin la fecha de
    metadatos ("[2026-10-01] Usuario: …") ni las líneas de Tresqu, que traen
    fechas de sus confirmaciones."""

    found: list[str] = []
    for block in lines:
        found.extend(m.group(1) for m in _MEMORY_USER_LINE.finditer(block or ""))
    return found


def resolve_new_record_date(
    value: str | None,
    today: date,
    day_texts: Iterable[str],
    year_texts: Iterable[str] | None = None,
) -> str | None:
    """Fecha de un registro NUEVO.

    Si el usuario no dio un día en ``day_texts`` (lo que escribió en el
    historial visible y lo que el agente buscó en la memoria), es de hoy. Es
    amplio a propósito: un "ayer" de otro mensaje deja pasar la fecha del
    modelo (lo de antes), pero así ninguna cadena de aclaraciones ("el 5 gasté
    en taxi" → "¿cuánto?" → "20000" → "¿moneda?" → "COP") pierde el día. Diga lo que
    diga el modelo. El 2026-10-05 el supervisor copió "hoy, 4 de octubre" de una
    confirmación vieja del historial y el subagente registró el ingreso dos
    veces, una por cada fecha. Si el usuario sí dio un día, solo se corrige el
    año con ``year_texts`` (``resolve_year``).
    """

    day_texts = [t or "" for t in (day_texts or [])]
    if user_gave_a_day(day_texts):
        return resolve_year(value, today, list(year_texts if year_texts is not None else day_texts))
    if value and value.strip()[:10] != today.isoformat():
        logger.warning("date_guard: nadie dio un día; %s -> hoy %s", value, today.isoformat())
        return today.isoformat()
    return value


def resolve_year(value: str | None, today: date, texts: Iterable[str]) -> str | None:
    """Devuelve ``value`` (YYYY-MM-DD) con el año corregido si nadie dio fecha."""

    if not value:
        return value
    try:
        parsed = datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return value
    if parsed.year > today.year - 2:
        return value
    if _user_gave_a_date([t or "" for t in (texts or [])]):
        return value

    for year in (today.year, today.year - 1):
        try:
            candidate = parsed.replace(year=year)
        except ValueError:  # 29 de febrero en un año no bisiesto
            continue
        if candidate <= today:
            logger.warning(
                "date_guard: nadie dio una fecha y el año %s es de hace años; %s -> %s",
                parsed.year, value, candidate.isoformat(),
            )
            return candidate.isoformat()
    return value
