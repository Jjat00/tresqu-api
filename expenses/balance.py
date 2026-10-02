"""Saldo de un usuario calculado en base de datos, nunca por el modelo.

"¿Cuánto me queda?" se contestaba con el modelo restando totales: el
2026-10-01 el saldo real era 1.660.000 − 1.752.900 = −92.900 COP y Tresqu dijo
−89.900. Aquí la suma y la resta las hace PostgreSQL / ``Decimal`` y el agente
solo narra el resultado.

Mismo criterio de fechas que el dashboard y que ``get_expense_totals``: fecha
real del movimiento (``spent_at`` / ``received_at``) y, si no la tiene, su
``timestamp`` en la zona horaria del usuario. Cada moneda va por separado:
restar COP de USD no tiene sentido.

Saldo inicial: cuando el usuario declara cuánta plata tiene ("tengo
1.660.000"), el agente lo registra como ingreso con la nota "saldo inicial".
Desde ahí esa moneda se cuenta de nuevo: lo anterior ya está reflejado en la
cifra declarada, y restarlo otra vez dejaba en −176.500 a quien tenía 808.000.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytz
from django.db.models import Count, Q, Sum

from expenses.models import Expense
from income.models import Income
from users.models import User

INITIAL_BALANCE_NOTE = "saldo inicial"


def _money(value: Decimal | None) -> float:
    return float((value or Decimal("0")).quantize(Decimal("0.01")))


def _user_tz(user: User):
    try:
        return pytz.timezone(user.timezone)
    except (AttributeError, pytz.exceptions.UnknownTimeZoneError):
        return pytz.timezone("America/Bogota")


def _initial_balance_q() -> Q:
    # Coincidencia exacta: una nota libre que solo menciona "saldo inicial"
    # ("Saldo inicial del mes registrado como…") no es un punto de partida.
    return Q(note__iexact=INITIAL_BALANCE_NOTE) | Q(category_str__iexact=INITIAL_BALANCE_NOTE)


def _effective_date(record, field: str, tz) -> date:
    value = getattr(record, field)
    return value or record.timestamp.astimezone(tz).date()


def _anchor_key(income: Income, tz):
    return (_effective_date(income, "received_at", tz), income.created_at, income.id)


def latest_initial_balances(user: User) -> dict[str, Income]:
    """El último saldo inicial declarado de cada moneda.

    Se ordena por su fecha (``received_at`` o, si no tiene, el día local de su
    ``timestamp``) y, dentro del mismo día, por cuándo se registró.
    """

    tz = _user_tz(user)
    anchors: dict[str, Income] = {}
    for income in Income.objects.filter(user=user).filter(_initial_balance_q()):
        current = anchors.get(income.currency)
        if current is None or _anchor_key(income, tz) > _anchor_key(current, tz):
            anchors[income.currency] = income
    return anchors


def _after_anchor(field: str, anchor: Income, tz) -> Q:
    """Movimientos posteriores a la declaración del saldo inicial.

    - Con fecha real: días posteriores y, del mismo día, solo lo registrado
      después de la declaración (lo de antes ya estaba en la cifra declarada).
    - Sin fecha real: igual, ubicándolos por su ``timestamp`` en la zona del
      usuario, como hace el dashboard.
    """

    anchor_day = _effective_date(anchor, "received_at", tz)
    day_start = tz.localize(datetime.combine(anchor_day, time.min)).astimezone(pytz.UTC)
    next_day_start = tz.localize(
        datetime.combine(anchor_day + timedelta(days=1), time.min)
    ).astimezone(pytz.UTC)
    dated = Q(**{f"{field}__gt": anchor_day}) | Q(
        **{field: anchor_day, "created_at__gt": anchor.created_at}
    )
    undated = Q(**{f"{field}__isnull": True}) & (
        Q(timestamp__gte=next_day_start)
        | Q(timestamp__gte=day_start, created_at__gt=anchor.created_at)
    )
    return dated | undated


def set_initial_balance(user: User, amount: Decimal, currency: str | None = None) -> Income:
    """Registra el saldo con el que el usuario empieza a contar.

    Va aparte de ``create_income`` porque un saldo inicial puede ser 0
    ("empieza a contar desde cero") y un ingreso normal no. Negativo no: quien
    arranca debiendo lo registra como 0 más el gasto o la deuda. Por lo demás
    se comporta igual: moneda validada, categoría del usuario, rastreo para
    vincularlo a la confirmación de WhatsApp y embedding para la búsqueda.
    """

    from django.utils import timezone

    from agents.run_context import record_created_transaction
    from categories.utils import get_or_create_user_income_category
    from telegrambot.currencies import is_valid_currency

    if amount < 0:
        raise ValueError("el saldo inicial no puede ser negativo")
    can_add, message = user.can_add_income()
    if not can_add:
        raise ValueError(message)
    if currency and not is_valid_currency(currency):
        raise ValueError(f"moneda no válida: {currency}")
    currency = (currency or getattr(user, "default_currency", None) or "COP").upper()

    category, _ = get_or_create_user_income_category(
        user=user,
        name="Saldo Inicial",
        description="Plata con la que empiezas a contar en Tresqu",
        example="Saldo inicial declarado",
    )
    now = timezone.now()
    received = now.astimezone(_user_tz(user)).date()
    text = f"Saldo inicial de {amount} {currency} el {received}."
    income = Income.objects.create(
        user=user,
        amount=amount,
        currency=currency,
        category_str=category.name,
        user_income_category=category,
        description="Saldo inicial declarado por el usuario",
        note=INITIAL_BALANCE_NOTE,
        raw_message=text,
        timestamp=now,
        received_at=received,
    )
    record_created_transaction("income", income.id)
    try:
        from telegrambot.tools import embeddings

        income.embedding = embeddings.embed_query(text)
        income.save(update_fields=["embedding"])
    except Exception:  # noqa: BLE001 — sin embedding el saldo igual vale
        pass
    return income


def _sum(query) -> dict[str, Any]:
    row = query.aggregate(total=Sum("amount"), count=Count("id"))
    return {"total": row["total"] or Decimal("0"), "count": row["count"]}


def compute_balance(
    user: User,
    start_date: str | None = None,
    end_date: str | None = None,
    whole_history: bool = False,
) -> dict[str, Any]:
    """Ingresos, gastos y saldo (ingresos − gastos) por moneda.

    Sin fechas es lo que la gente entiende por "cuánto me queda": cada moneda
    desde su último saldo inicial declarado, o todo lo registrado si nunca lo
    declaró (o si se pide ``whole_history``). Con fechas (YYYY-MM-DD,
    inclusive), solo ese período y sin saldos iniciales de por medio.
    """

    from telegrambot.tools import _filter_by_period

    tz = _user_tz(user)
    use_anchors = not start_date and not end_date and not whole_history
    anchors = latest_initial_balances(user) if use_anchors else {}

    expenses = _filter_by_period(
        Expense.objects.filter(user=user), "spent_at", start_date, end_date, user=user
    )
    incomes = _filter_by_period(
        Income.objects.filter(user=user), "received_at", start_date, end_date, user=user
    )

    default_currency = getattr(user, "default_currency", None) or "COP"
    currencies = set(expenses.values_list("currency", flat=True).distinct())
    currencies |= set(incomes.values_list("currency", flat=True).distinct())
    currencies = sorted(currencies or {default_currency}, key=lambda c: (c != default_currency, c))

    by_currency = []
    for currency in currencies:
        exp_qs = expenses.filter(currency=currency)
        inc_qs = incomes.filter(currency=currency)
        anchor = anchors.get(currency)
        if anchor:
            exp_qs = exp_qs.filter(_after_anchor("spent_at", anchor, tz))
            # Cuenta el ancla y lo posterior; ni lo anterior ni otros saldos iniciales.
            inc_qs = inc_qs.filter(
                Q(id=anchor.id)
                | (_after_anchor("received_at", anchor, tz) & ~_initial_balance_q())
            )
        inc, exp = _sum(inc_qs), _sum(exp_qs)
        by_currency.append({
            "currency": currency,
            "since_initial_balance": (
                _effective_date(anchor, "received_at", tz).isoformat() if anchor else None
            ),
            "initial_balance": _money(anchor.amount) if anchor else None,
            "incomes_total": _money(inc["total"]),
            "incomes_count": inc["count"],
            "expenses_total": _money(exp["total"]),
            "expenses_count": exp["count"],
            "balance": _money(inc["total"] - exp["total"]),
        })

    if start_date or end_date:
        label = f"{start_date or 'inicio'} a {end_date or 'hoy'}"
    elif anchors:
        label = "cada moneda desde su último saldo inicial (since_initial_balance); sin él, todo lo registrado"
    else:
        label = "todo lo registrado"
    return {
        "period": {"from": start_date, "to": end_date, "label": label},
        "default_currency": default_currency,
        "by_currency": by_currency,
        "note": (
            "balance = incomes_total - expenses_total, calculado en base de datos. "
            "Repórtalo tal cual, diciendo desde cuándo cuenta cada moneda; no lo "
            "recalcules ni lo combines entre monedas."
        ),
    }
