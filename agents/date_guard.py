"""Defensa determinista contra años inventados por el modelo.

El 2026-10-01 el usuario escribió "12000 gaseosa" y el gasto quedó con fecha
2023-10-01: el modelo puso un año que nadie dijo. Igual que con la moneda
(``currency_guard``), aquí no se le pregunta nada al modelo.

Es deliberadamente conservador, porque cambiar una fecha bien puesta es tan
malo como dejar una mal puesta:

- Solo toca años de hace DOS o más años. El año pasado sale de frases que
  no llevan el número ("el 15 de marzo del año pasado", "en diciembre" dicho en
  enero) y las fechas futuras de "mañana" un 31 de diciembre son legítimas.
- No toca nada si el año aparece escrito en la conversación, ni si hay una
  referencia relativa a años ("hace dos años", "el año antepasado").
- Solo se aplica al CREAR: al editar, la fecha viene del registro guardado.
- Si la fecha corregida no existe (29 de febrero) o quedaría en el futuro, se
  prueba con el año anterior; si tampoco, la fecha se deja como estaba.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Iterable

logger = logging.getLogger(__name__)

# Cualquier referencia relativa a años o meses en el mismo mensaje ("hace seis años",
# "hace aproximadamente unos seis o siete años", "hace 2.5 años", "el año
# antepasado", "años atrás", "hace 30 meses", "three years ago"): ante la duda,
# la fecha del modelo se respeta.
_RELATIVE_YEARS = re.compile(
    r"\bhace\b.*\b(?:años?|mes(?:es)?)\b|antepasado|\b(?:años?|meses)\s+atr[aá]s\b|"
    r"\b(?:years?|months?)\s+ago\b|\blast\s+year\b",
    re.IGNORECASE | re.DOTALL,
)


def _year_mentioned(year: int, texts: list[str]) -> bool:
    short = f"{year % 100:02d}"
    long_pattern = re.compile(rf"(?<!\d){year}(?!\d)")
    # Año corto en una fecha (15/03/23) o con apóstrofo ('23). "del 23" no
    # cuenta: casi siempre es un día ("el gasto del 23 de marzo").
    short_pattern = re.compile(rf"(?:\d{{1,2}}[/-]\d{{1,2}}[/-]|')({short})(?!\d)")
    return any(long_pattern.search(t) or short_pattern.search(t) for t in texts)


def resolve_year(value: str | None, today: date, texts: Iterable[str]) -> str | None:
    """Devuelve ``value`` (YYYY-MM-DD) con el año corregido si nadie lo dijo."""

    if not value:
        return value
    try:
        parsed = datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return value
    if parsed.year > today.year - 2:
        return value
    texts = [t or "" for t in (texts or [])]
    if _year_mentioned(parsed.year, texts) or any(_RELATIVE_YEARS.search(t) for t in texts):
        return value

    for year in (today.year, today.year - 1):
        try:
            candidate = parsed.replace(year=year)
        except ValueError:  # 29 de febrero en un año no bisiesto
            continue
        if candidate <= today:
            logger.warning(
                "date_guard: el año %s no aparece en la conversación; %s -> %s",
                parsed.year, value, candidate.isoformat(),
            )
            return candidate.isoformat()
    return value
