"""Defensa determinista contra registrar el mismo movimiento dos veces en un turno.

El 2026-10-02 ("20000 cervezas") y el 2026-10-05 ("200000 del préstamo Darwin
ya me los devolvió") el subagente lanzó dos creaciones en paralelo para UN
movimiento: el supervisor le pasó "hoy, 4 de octubre" (un "hoy" copiado de
una confirmación vieja del historial) y el modelo cubrió las dos fechas.
Igual que con la moneda y la fecha, aquí no se le pregunta nada al modelo.

Regla: en un mismo turno, un gasto (o ingreso) del mismo monto y moneda se
registra a lo sumo tantas veces como el usuario escribió ese monto en su
mensaje, y como mínimo una. "200000 préstamo Darwin" → una vez; "20000 almuerzo
y 20000 taxi" → dos. Si el usuario pide repetir de forma explícita ("cada
uno", "dos veces", "x3"), no hay tope. La comprobación y la creación van bajo
un candado: las tools síncronas corren en hilos y las dos llamadas paralelas
llegan a la vez.
"""

from __future__ import annotations

import logging
import re
import threading
from decimal import Decimal, InvalidOperation
from typing import Callable

logger = logging.getLogger(__name__)

_REPEAT = re.compile(
    r"\bcada\s+un[oa]?\b|\bveces\b|\bx\s?\d+\b|\b\d+\s?x\b|\bambos\b|\bambas\b"
    r"|\blos\s+dos\b|\blas\s+dos\b|\brepet\w*|\bduplic\w*",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d.,]*")


def _as_decimal(text: str) -> Decimal | None:
    """Un número escrito por el usuario: '200000', '200.000', '200,000', '20000.5'."""
    digits = text.rstrip(".,")
    # Con separadores de miles ("200.000", "1,250,000") se quitan; un único
    # separador con 1-2 decimales se toma como decimal ("12.5", "9,99").
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", digits):
        digits = re.sub(r"[.,]", "", digits)
    else:
        digits = digits.replace(",", ".")
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def amount_mentions(message: str, amount: Decimal) -> int:
    """Cuántas veces aparece ``amount`` escrito en el mensaje."""
    count = 0
    for match in _NUMBER.finditer(message or ""):
        value = _as_decimal(match.group())
        if value is not None and value == amount:
            count += 1
    return count


class TurnCreations:
    """Lo creado en un turno, para no registrar dos veces el mismo movimiento."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message or ""
        self._lock = threading.Lock()
        self._created: dict[tuple[str, Decimal, str], list[str]] = {}

    def _limit(self, amount: Decimal) -> int | None:
        if _REPEAT.search(self.user_message):
            return None
        return max(1, amount_mentions(self.user_message, amount))

    def create(self, kind: str, amount, currency: str, do_create: Callable[[], str]) -> str:
        """Ejecuta ``do_create`` salvo que ya se haya registrado lo mismo en este turno."""
        try:
            value = Decimal(str(amount))
        except (InvalidOperation, ValueError):
            return do_create()  # la tool base rechaza el monto inválido
        key = (kind, value, (currency or "").upper())
        with self._lock:
            done = self._created.get(key, [])
            limit = self._limit(value)
            if limit is not None and len(done) >= limit:
                logger.warning(
                    "duplicate_guard: %s de %s ya registrado en este turno; se descarta otro",
                    kind, value,
                )
                label = "gasto" if kind == "expense" else "ingreso"
                return (
                    f"Error: NO registrado. Ya registraste en este turno un {label} de "
                    f"{value} ({done[0][:120]}). El usuario mencionó un solo movimiento: "
                    "no lo dupliques ni cubras dos fechas posibles. Reporta solo el "
                    "registro ya hecho."
                )
            result = do_create()
            if isinstance(result, str) and not result.startswith("Error"):
                self._created.setdefault(key, []).append(result)
            return result
