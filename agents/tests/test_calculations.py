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
    from langchain_core.messages import AIMessage, HumanMessage

    from agents.currency_guard import user_texts
    from agents.date_guard import resolve_year

    print("\n[año de las fechas]")
    today = date(2026, 10, 1)
    # El caso real: "12000 gaseosa" quedó con fecha 2023-10-01.
    _check(
        "sin fecha del usuario, un año viejo pasa al actual",
        resolve_year("2023-10-01", today, ["12000 gaseosa"]) == "2026-10-01",
    )
    _check("'ayer' también se corrige", resolve_year("2023-09-30", today, ["ayer 5000 mecato"]) == "2026-09-30")
    _check("el año actual no se toca", resolve_year("2026-09-28", today, ["el domingo"]) == "2026-09-28")
    _check("el año pasado no se toca", resolve_year("2025-03-15", today, ["algo"]) == "2025-03-15")
    _check(
        "'mañana' un 31 de diciembre no se toca",
        resolve_year("2027-01-01", date(2026, 12, 31), ["mañana"]) == "2027-01-01",
    )
    for phrase in (
        "gasté 50 mil el 15 de marzo de 2023", "pagué el 15/03/23", "Pagué 500 el 15 de marzo del 24",
        "Pagué 500 hace 800 días", "eso fue hace tres años", "hace un par de años",
        "pagué 500 hace aproximadamente unos seis o siete años", "Pagué 500 hace 30 meses",
        "I paid 500 three years ago", "el año antepasado", "el gasto del 23", "en marzo",
        "Pagué 500 en el 24", "Pagué 500 en dos mil veinticuatro", "recibí 300 en el año 23",
        "the 2023 trip, twenty twenty-three", "Pagué 500 en el veinticuatro", "el quince de este",
    ):
        _check(
            f"con fecha del usuario se respeta: {phrase!r}",
            resolve_year("2023-03-15", today, [phrase]) == "2023-03-15",
        )
    _check(
        "si con el año actual quedaría en el futuro, va al anterior",
        resolve_year("2023-12-28", today, ["5000 mecato"]) == "2025-12-28",
    )
    _check(
        "un 29 de febrero sin año válido se deja igual (nunca 'hoy')",
        resolve_year("2024-02-29", date(2027, 3, 1), ["5000 mecato"]) == "2024-02-29",
    )
    _check("sin fecha no hace nada", resolve_year(None, today, []) is None)
    _check("una fecha rara se deja igual", resolve_year("ayer", today, []) == "ayer")

    history = [
        HumanMessage(content="48000 hamburguesa"),
        AIMessage(content="Registré 48.000 COP hoy, 1 de octubre de 2026."),
    ]
    texts = user_texts("12000 gaseosa", history)
    _check("user_texts deja fuera a Tresqu", texts == ["48000 hamburguesa", "12000 gaseosa"])
    _check(
        "y así la fecha de una confirmación de Tresqu no frena la corrección",
        resolve_year("2023-10-01", today, texts) == "2026-10-01",
    )
    long_history = [HumanMessage(content="Registra un gasto de 500 del 15/03/2024")] + [
        AIMessage(content="¿Categoría?"), HumanMessage(content="Comida"),
    ] * 5
    _check(
        "una fecha vieja del usuario sigue contando aunque el hilo sea largo",
        resolve_year("2024-03-15", today, user_texts("Sí, regístralo", long_history)) == "2024-03-15",
    )
    _check(
        "acepta el historial del chat web como dicts",
        user_texts("x", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]) == ["a", "x"],
    )


def main() -> int:
    import django
    import os

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()
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
