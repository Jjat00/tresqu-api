"""Cálculos deterministas: ``agents.calculator`` y ``agents.date_guard``.

    python -m agents.tests.test_calculations

Sin base de datos ni OpenAI.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

_FAILURES: list[str] = []


def _check(label: str, condition: bool) -> None:
    print(f"{'✅' if condition else '❌'} {label}")
    if not condition:
        _FAILURES.append(label)


def _run_calculator() -> None:
    from agents.calculator import CalculationError, _calculate_tool_impl, calculate

    print("\n[calculadora]")
    # El caso real: el modelo respondió −89.900.
    _check("1660000 - 1752900 = -92900", calculate("1660000 - 1752900") == Decimal("-92900"))
    _check("acepta miles con punto", calculate("1.660.000 - 1.752.900") == Decimal("-92900"))
    _check("acepta miles con coma", calculate("1,660,000 - 1,752,900") == Decimal("-92900"))
    _check("coma decimal", calculate("12,75 * 2") == Decimal("25.50"))
    _check("porcentaje", calculate("3500000 * 20%") == Decimal("700000"))
    _check("decimales exactos", calculate("0.1 + 0.2") == Decimal("0.3"))
    _check("acciones fraccionarias", calculate("0.02598 * 3") == Decimal("0.07794"))
    _check("paréntesis y división", calculate("(120000 + 45000) / 3") == Decimal("55000"))
    _check("signos tipográficos", calculate("10 × 3 − 4 ÷ 2") == Decimal("28"))
    _check("redondeo legible", _calculate_tool_impl("10 / 3") == "3.333333")

    for bad in ("852.000 + 1", "1660000 - 852,000", '__import__("os")', "abs(-1)", "x + 1", "1/0", "2 ** 100", "", "1 if 1 else 2"):
        try:
            calculate(bad)
            ok = False
        except CalculationError:
            ok = True
        _check(f"rechaza {bad!r}", ok)
    _check("la tool no revienta con un error", _calculate_tool_impl("1/0").startswith("error"))
    _check(
        "ni con un resultado gigante",
        _calculate_tool_impl("999999999999 ** 12").startswith("error"),
    )


def _run_date_guard() -> None:
    from agents.date_guard import resolve_year

    print("\n[año de las fechas]")
    today = date(2026, 10, 1)
    # El caso real: "12000 gaseosa" quedó con fecha 2023-10-01.
    _check(
        "un año que nadie dijo pasa al actual",
        resolve_year("2023-10-01", today, ["12000 gaseosa"]) == "2026-10-01",
    )
    _check("el año actual no se toca", resolve_year("2026-09-28", today, ["el domingo"]) == "2026-09-28")
    _check(
        "un año que el usuario escribió se respeta",
        resolve_year("2025-03-15", today, ["gasté 50 mil el 15 de marzo de 2025"]) == "2025-03-15",
    )
    _check(
        "también con año corto en la fecha",
        resolve_year("2025-03-15", today, ["pagué el 15/03/25"]) == "2025-03-15",
    )
    _check(
        "si con el año actual quedaría en el futuro, va al anterior",
        resolve_year("2024-12-28", date(2026, 1, 5), ["el 28 de diciembre"]) == "2025-12-28",
    )
    _check("sin fecha no hace nada", resolve_year(None, today, []) is None)
    _check("una fecha rara se deja igual", resolve_year("ayer", today, []) == "ayer")


def main() -> int:
    _run_calculator()
    _run_date_guard()
    print()
    if _FAILURES:
        print(f"❌ {len(_FAILURES)} comprobaciones fallaron:")
        for failure in _FAILURES:
            print(f"   - {failure}")
        return 1
    print("✅ todas las comprobaciones pasaron")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
