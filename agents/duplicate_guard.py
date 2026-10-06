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

# Repetición pedida de forma afirmativa ("cada uno", "dos veces", "x3").
# Palabras sueltas como "veces" o "duplicar" no bastan: "a veces tomo taxi" o
# "sin duplicar" no piden repetir nada.
_REPEAT = re.compile(
    r"\bcada\s+un[oa]\b"
    r"|\b(?:dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|\d+)\s+veces\b"
    r"|(?<![\w.,])x\s?\d+\b|\b\d+\s?x\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:no|sin|nunca|ni)\b[^.;\n]{0,25}$", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d.,]*")
# Un monto se escribe a veces abreviado: "20k", "20 mil", "1,5M", "2 millones"
# o, en monedas de alta denominación, sin los miles ("20 almuerzo" = 20.000).
_SCALES = (Decimal(1), Decimal(1000), Decimal(1000000))


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
    """Cuántas veces el mensaje escribe ``amount``, entero o abreviado.

    Se cuenta de más antes que de menos: un tope alto deja pasar un duplicado
    (lo de antes), uno bajo bloquearía un movimiento legítimo.
    """
    count = 0
    for match in _NUMBER.finditer(message or ""):
        value = _as_decimal(match.group())
        if value is not None and any(value * scale == amount for scale in _SCALES):
            count += 1
    return count


def asks_for_repetition(message: str) -> bool:
    for match in _REPEAT.finditer(message or ""):
        if not _NEGATION.search(message[: match.start()]):
            return True
    return False


class TurnCreations:
    """Lo creado en un turno, para no registrar dos veces el mismo movimiento."""

    def __init__(self, user_message: str | None):
        self.user_message = user_message or ""
        self._lock = threading.Lock()
        self._created: dict[tuple[str, Decimal, str], list[str]] = {}

    def _limit(self, amount: Decimal) -> int | None:
        if asks_for_repetition(self.user_message):
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
