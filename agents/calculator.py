"""Aritmética determinista para los agentes.

El modelo no sabe restar: el 2026-10-01 un usuario preguntó "¿cuánto me
queda?", el saldo real era 1.660.000 − 1.752.900 = −92.900 COP y Tresqu
respondió −89.900. Los totales y el saldo salen de la base de datos
(``expenses.balance``); cualquier otra cuenta que haga falta en una respuesta
(un porcentaje, una diferencia entre dos totales, una cuota) pasa por aquí.

``calculate`` evalúa una expresión aritmética con ``Decimal`` recorriendo su
árbol sintáctico: solo admite números, paréntesis y ``+ - * / // % **``. Nada
de nombres, llamadas ni atributos, así que no hay forma de ejecutar código.
"""

from __future__ import annotations

import ast
import operator
import re
from decimal import Decimal, DecimalException, DivisionByZero, Inexact, InvalidOperation, Overflow, localcontext

_MAX_EXPRESSION_CHARS = 300
_MAX_EXPONENT = 12
_PRECISION = 34
# Ningún número de una cuenta personal se acerca a esto; cortar antes evita
# que potencias anidadas revienten el contexto decimal.
_MAX_MAGNITUDE = Decimal(10) ** 24
# Decimales que se muestran: solo importa en divisiones periódicas (10/3).
_MAX_DECIMALS = 12
_AMBIGUOUS = re.compile(r"(?<![\d.,])[1-9]\d{0,2}[.,]\d{3}(?![\d.,])")

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class CalculationError(ValueError):
    """La expresión no es aritmética válida o no se puede calcular."""


def _normalize(expression: str) -> str:
    """Acepta lo que escribe un modelo: "1.660.000 - 1.752.900", "20%", "×"."""

    text = (expression or "").strip()
    text = text.replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    # Miles con punto o coma ("1.660.000", "1,660,000"): se quitan los separadores.
    text = re.sub(r"\d{1,3}(?:\.\d{3}){2,}", lambda m: m.group(0).replace(".", ""), text)
    text = re.sub(r"\d{1,3}(?:,\d{3}){2,}", lambda m: m.group(0).replace(",", ""), text)
    # "852.000" o "852,000" puede ser 852 mil o 852 con decimales: no se adivina.
    if _AMBIGUOUS.search(text):
        raise CalculationError(
            "número ambiguo (¿miles o decimales?): escríbelo sin separador de miles, p. ej. 852000"
        )
    # Coma decimal en español ("0,5", "12,75").
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    # "20%" → "(20/100)". Un % seguido de otro número es el operador módulo.
    text = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(.])", r"(\1/100)", text)
    return text


def _checked(value: Decimal) -> Decimal:
    if abs(value) > _MAX_MAGNITUDE:
        raise CalculationError("el resultado está fuera de rango")
    return value


def _eval(node, source: str):
    if isinstance(node, ast.Expression):
        return _eval(node.body, source)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        # Desde el texto del literal, no desde el float que ya redondeó Python:
        # 0.123456789123456789 no cabe en un float.
        literal = ast.get_source_segment(source, node) or str(node.value)
        return _checked(Decimal(literal))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left, source), _eval(node.right, source)
        if isinstance(node.op, ast.Pow) and (abs(right) > _MAX_EXPONENT or right != right.to_integral_value()):
            raise CalculationError("solo se admiten potencias enteras pequeñas")
        return _checked(_BINARY[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, source))
    raise CalculationError("solo se admiten números, paréntesis y + - * / // % **")


def _evaluate(expression: str) -> tuple[Decimal, bool]:
    """``(resultado, inexacto)``. ``inexacto`` solo si hubo que redondear (10/3)."""

    # El límite va ANTES de normalizar: las regex no deben ver entradas enormes.
    if not expression or len(expression) > _MAX_EXPRESSION_CHARS:
        raise CalculationError("expresión vacía o demasiado larga")
    text = _normalize(expression)
    if not text or len(text) > _MAX_EXPRESSION_CHARS * 2:
        raise CalculationError("expresión vacía o demasiado larga")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalculationError(f"expresión inválida: {expression!r}") from exc
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        ctx.traps[DivisionByZero] = True
        ctx.traps[InvalidOperation] = True
        ctx.traps[Overflow] = True
        ctx.clear_flags()
        try:
            value = _eval(tree, text)
        except (DecimalException, ZeroDivisionError, OverflowError) as exc:
            raise CalculationError("división por cero, desborde u operación inválida") from exc
        return value, bool(ctx.flags[Inexact])


def calculate(expression: str) -> Decimal:
    """Evalúa ``expression`` y devuelve un ``Decimal``.

    Exacto salvo cuando el resultado no se puede escribir con 34 dígitos
    (divisiones periódicas). Lanza ``CalculationError`` si no es aritmética
    válida, si divide por cero o si se desborda.
    """

    return _evaluate(expression)[0]


def format_result(value: Decimal, inexact: bool = False) -> str:
    """Resultado sin notación científica.

    Un resultado exacto se muestra completo, con todos sus decimales. Solo uno
    inexacto (10/3) se aproxima, a ``_MAX_DECIMALS`` decimales.
    """

    exponent = value.as_tuple().exponent
    if inexact and isinstance(exponent, int) and exponent < -_MAX_DECIMALS:
        with localcontext() as ctx:
            ctx.prec = _PRECISION
            value = value.quantize(Decimal(1).scaleb(-_MAX_DECIMALS))
    # format(…, "f") no redondea; normalize() sí (usa el contexto de 28
    # dígitos), así que los ceros sobrantes se quitan sobre el texto.
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "", "-") else text


def _calculate_tool_impl(expression: str) -> str:
    try:
        value, inexact = _evaluate(expression)
        result = format_result(value, inexact)
        return f"{result} (aproximado)" if inexact else result
    except CalculationError as exc:
        return f"error: {exc}"
    except (DecimalException, OverflowError):
        return "error: el resultado está fuera de rango"


def _build_calculate_tool():
    from langchain_core.tools import tool

    @tool("calculate")
    def calculate_tool(expression: str) -> str:
        """Calcula una expresión aritmética de forma EXACTA y devuelve el resultado.

        Úsala para CUALQUIER cuenta que no traiga ya hecha otra tool: diferencias,
        porcentajes, divisiones, cuotas, conversiones con una tasa dada. Nunca
        hagas cuentas de cabeza. Escribe los números sin separador de miles y con
        punto decimal: "1660000 - 1752900", "3500000 * 20%", "(120000 + 45000) / 3".
        Admite + - * / // % ** y paréntesis. "15%" es porcentaje; "10 % 3" es
        módulo (con un negativo, entre paréntesis: "10 % (-3)")."""
        return _calculate_tool_impl(expression)

    return calculate_tool


calculate_tool = _build_calculate_tool()
