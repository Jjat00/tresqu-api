"""Defensa determinista contra años inventados por el modelo.

El 2026-10-01 el usuario escribió "12000 gaseosa" y el gasto quedó con fecha
2023-10-01: el modelo puso un año que nadie dijo. Igual que con la moneda
(``currency_guard``), aquí no se le pregunta nada al modelo: si el año de la
fecha no aparece en el texto real de la conversación, se cambia por el año
actual del usuario (o por el anterior si así la fecha quedaría en el futuro,
p. ej. "el 28 de diciembre" dicho en enero).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Iterable

logger = logging.getLogger(__name__)


def _year_mentioned(year: int, texts: Iterable[str]) -> bool:
    short = f"{year % 100:02d}"
    long_pattern = re.compile(rf"(?<!\d){year}(?!\d)")
    # "/23" o "-23" al final de una fecha corta (15/03/23).
    short_pattern = re.compile(rf"\d{{1,2}}[/-]\d{{1,2}}[/-]{short}(?!\d)")
    return any(long_pattern.search(t or "") or short_pattern.search(t or "") for t in texts)


def resolve_year(value: str | None, today: date, texts: Iterable[str]) -> str | None:
    """Devuelve ``value`` (YYYY-MM-DD) con el año corregido si nadie lo dijo."""

    if not value:
        return value
    try:
        parsed = datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return value
    texts = list(texts or [])
    if parsed.year == today.year or _year_mentioned(parsed.year, texts):
        return value

    def _with_year(year: int) -> date | None:
        try:
            return parsed.replace(year=year)
        except ValueError:  # 29 de febrero en un año no bisiesto
            return None

    fixed = _with_year(today.year)
    if fixed is None or fixed > today:
        fixed = _with_year(today.year - 1) or today
    logger.warning(
        "date_guard: el año %s no aparece en la conversación; %s -> %s",
        parsed.year, value, fixed.isoformat(),
    )
    return fixed.isoformat()
