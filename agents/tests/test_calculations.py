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
    _check(
        "división periódica: 12 decimales y marcada como aproximada",
        _calculate_tool_impl("10 / 3") == "3.333333333333 (aproximado)",
    )
    _check("sin redondeo oculto", _calculate_tool_impl("0.00000049 * 1") == "0.00000049")
    _check("un exacto largo sale completo", _calculate_tool_impl("0.02598123456789 * 1") == "0.02598123456789")
    _check("ni lo diminuto se vuelve 0", _calculate_tool_impl("0.00000000000049 * 1") == "0.00000000000049")
    _check("una división exacta no se marca", _calculate_tool_impl("1 / 8") == "0.125")
    _check(
        "más de 28 dígitos significativos salen completos",
        _calculate_tool_impl("0.1234567890123456789012345678901234 * 1")
        == "0.1234567890123456789012345678901234",
    )
    _check("enteros con ceros no pierden los ceros", _calculate_tool_impl("1000 * 100") == "100000")
    _check("un resultado diminuto no inunda la respuesta", _calculate_tool_impl("1e-1000000").startswith("error"))
    _check("módulo con negativo entre paréntesis", calculate("10 % (-3)") == Decimal("1"))
    _check(
        "literales sin pasar por float",
        calculate("0.123456789123456789 * 1000000000000000000") == Decimal("123456789123456789"),
    )
    _check("% seguido de número es módulo", calculate("10 % 3") == Decimal("1"))
    _check("% al final es porcentaje", calculate("200 * 15 %") == Decimal("30"))

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
    _check(
        "ni con potencias anidadas",
        _calculate_tool_impl("((((((10 ** 12) ** 12) ** 12) ** 12) ** 12) ** 12)").startswith("error"),
    )
    import time

    started = time.monotonic()
    result = _calculate_tool_impl("1" * 30000 + "%")
    _check(
        "una entrada enorme se rechaza de inmediato",
        result.startswith("error") and time.monotonic() - started < 0.1,
    )


def _run_date_guard() -> None:
    from agents.date_guard import resolve_year

    print("\n[año de las fechas]")
    today = date(2026, 10, 1)
    # El caso real: "12000 gaseosa" quedó con fecha 2023-10-01.
    _check(
        "un año viejo que nadie dijo pasa al actual",
        resolve_year("2023-10-01", today, ["12000 gaseosa"]) == "2026-10-01",
    )
    _check("el año actual no se toca", resolve_year("2026-09-28", today, ["el domingo"]) == "2026-09-28")
    _check(
        "el año pasado no se toca ('del año pasado' no lleva número)",
        resolve_year("2025-03-15", today, ["el 15 de marzo del año pasado"]) == "2025-03-15",
    )
    _check(
        "'mañana' un 31 de diciembre no se toca",
        resolve_year("2027-01-01", date(2026, 12, 31), ["mañana"]) == "2027-01-01",
    )
    _check(
        "un año que el usuario escribió se respeta",
        resolve_year("2023-03-15", today, ["gasté 50 mil el 15 de marzo de 2023"]) == "2023-03-15",
    )
    _check(
        "también con año corto en la fecha",
        resolve_year("2023-03-15", today, ["pagué el 15/03/23"]) == "2023-03-15",
    )
    _check(
        "'del 23 de marzo' no cuenta como año 2023",
        resolve_year("2023-03-23", today, ["el gasto del 23 de marzo"]) == "2026-03-23",
    )
    for phrase in ("eso fue hace tres años", "gasté 50 hace seis años, el 1 de mayo",
                   "hace como diez años", "hace un par de años", "el año antepasado",
                   "pagué 500 hace aproximadamente unos seis o siete años", "pagué 500 hace 2.5 años",
                   "Pagué 500 hace 30 meses", "I paid 500 three years ago"):
        _check(
            f"referencia relativa respetada: {phrase!r}",
            resolve_year("2020-05-01", today, [phrase]) == "2020-05-01",
        )
    _check(
        "si con el año actual quedaría en el futuro, va al anterior",
        resolve_year("2023-12-28", today, ["el 28 de diciembre"]) == "2025-12-28",
    )
    _check(
        "un 29 de febrero sin año válido se deja igual (nunca 'hoy')",
        resolve_year("2024-02-29", today, ["el 29 de febrero"]) == "2024-02-29",
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
