"""Saldo de un usuario calculado en base de datos, nunca por el modelo.

"¿Cuánto me queda?" se contestaba con el modelo restando totales: el
2026-10-01 el saldo real era 1.660.000 − 1.752.900 = −92.900 COP y Tresqu dijo
−89.900. Aquí la suma y la resta las hace PostgreSQL / ``Decimal`` y el agente
solo narra el resultado.

Mismo criterio de fechas que el dashboard y que ``get_expense_totals``: fecha
real del movimiento (``spent_at`` / ``received_at``) y, si no la tiene, su
``timestamp`` en la zona horaria del usuario. Cada moneda va por separado:
restar COP de USD no tiene sentido.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Count, Q, Sum

from expenses.models import Expense
from income.models import Income
from users.models import User


def _money(value: Decimal | None) -> float:
    return float((value or Decimal("0")).quantize(Decimal("0.01")))


def _by_currency(query) -> dict[str, dict[str, Any]]:
    rows = query.values("currency").annotate(total=Sum("amount"), count=Count("id"))
    return {
        r["currency"]: {"total": r["total"] or Decimal("0"), "count": r["count"]}
        for r in rows
    }


_INITIAL_BALANCE = "saldo inicial"


def _initial_balance_q() -> Q:
    # Coincidencia exacta: una nota libre que solo menciona "saldo inicial"
    # ("Saldo inicial del mes registrado como…") no es un punto de partida.
    return Q(note__iexact=_INITIAL_BALANCE) | Q(category_str__iexact=_INITIAL_BALANCE)


def latest_initial_balance(user: User) -> Income | None:
    """El último ingreso que el usuario declaró como su saldo de partida.

    Cuando alguien dice "tengo 1.660.000" o "mi saldo es…", el agente lo
    registra como ingreso con la nota "saldo inicial". Desde ese punto el
    usuario cuenta su plata de nuevo: los gastos de antes ya están reflejados
    en esa cifra y restarlos otra vez lo deja en negativo sin razón (caso real
    del 2026-10-01: −92.900 en vez de 808.000).
    """

    return (
        Income.objects.filter(user=user, received_at__isnull=False)
        .filter(_initial_balance_q())
        .order_by("-received_at", "-id")
        .first()
    )


def compute_balance(
    user: User,
    start_date: str | None = None,
    end_date: str | None = None,
    whole_history: bool = False,
) -> dict[str, Any]:
    """Ingresos, gastos y saldo (ingresos − gastos) por moneda.

    Sin fechas es lo que la gente entiende por "cuánto me queda": desde su
    último saldo inicial si lo declaró, o todo lo registrado si no (o si se
    pide ``whole_history``). Con fechas (YYYY-MM-DD, inclusive), solo ese
    período.
    """

    from telegrambot.tools import _filter_by_period

    anchor = None
    if not start_date and not end_date and not whole_history:
        anchor = latest_initial_balance(user)

    expense_qs = Expense.objects.filter(user=user)
    income_qs = Income.objects.filter(user=user)
    if anchor:
        start_date = anchor.received_at.isoformat()
        # Saldos iniciales anteriores del mismo día ya no cuentan: los
        # reemplaza el último.
        income_qs = income_qs.exclude(Q(_initial_balance_q()) & ~Q(id=anchor.id))

    expenses = _by_currency(
        _filter_by_period(expense_qs, "spent_at", start_date, end_date, user=user)
    )
    incomes = _by_currency(
        _filter_by_period(income_qs, "received_at", start_date, end_date, user=user)
    )

    default_currency = getattr(user, "default_currency", None) or "COP"
    currencies = sorted(
        set(expenses) | set(incomes) or {default_currency},
        key=lambda c: (c != default_currency, c),
    )
    zero = {"total": Decimal("0"), "count": 0}
    by_currency = []
    for currency in currencies:
        inc = incomes.get(currency, zero)
        exp = expenses.get(currency, zero)
        by_currency.append({
            "currency": currency,
            "incomes_total": _money(inc["total"]),
            "incomes_count": inc["count"],
            "expenses_total": _money(exp["total"]),
            "expenses_count": exp["count"],
            "balance": _money(inc["total"] - exp["total"]),
        })

    if anchor:
        label = (
            f"desde tu saldo inicial de {_money(anchor.amount):,.0f} {anchor.currency} "
            f"del {anchor.received_at.isoformat()}"
        ).replace(",", ".")
    elif not start_date and not end_date:
        label = "todo lo registrado"
    else:
        label = f"{start_date or 'inicio'} a {end_date or 'hoy'}"
    return {
        "period": {"from": start_date, "to": end_date, "label": label},
        "starts_from_initial_balance": bool(anchor),
        "default_currency": default_currency,
        "by_currency": by_currency,
        "note": (
            "balance = incomes_total - expenses_total, calculado en base de datos. "
            "Repórtalo tal cual, diciendo el período (period.label); no lo recalcules "
            "ni lo combines entre monedas."
        ),
    }
