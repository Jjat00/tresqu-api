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
    r"|\bdos\s+mil\b|\bmil\s+novecientos\b|\btwenty\b|\bnineteen\b"  # años con palabras
    r"|\bhace\b|\batr[aá]s\b|\bantepasad|\bpasad[oa]\b|\banterior\b"
    r"|\b(?:años?|mes(?:es)?|semanas?|d[ií]as)\b"
    r"|\b(?:ago|last|years?|months?|weeks?|days)\b",
    re.IGNORECASE,
)


def _user_gave_a_date(texts: list[str]) -> bool:
    return any(_EXPLICIT_DATE.search(t) for t in texts)


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
