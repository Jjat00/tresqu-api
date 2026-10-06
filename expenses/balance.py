"""Saldo de un usuario calculado en base de datos, nunca por el modelo.

"¿Cuánto me queda?" se contestaba con el modelo restando totales: el
2026-10-01 el saldo real era 1.660.000 − 1.752.900 = −92.900 COP y Tresqu dijo
−89.900. Aquí la suma y la resta las hace PostgreSQL / ``Decimal`` y el agente
solo narra el resultado.

Mismo criterio de fechas que el dashboard y que ``get_expense_totals``: fecha
real del movimiento (``spent_at`` / ``received_at``) y, si no la tiene, su
``timestamp`` en la zona horaria del usuario. Cada moneda va por separado:
restar COP de USD no tiene sentido.

Por defecto el saldo es el del mes actual (ver ``compute_balance``).

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
# Un saldo inicial declarado en los últimos días del mes anterior se arrastra
# al saldo del mes: quien dijo "tengo 1.660.000" el 30 de septiembre espera
# que su saldo de octubre parta de ahí (decisión de Jaime, 2026-10-06).
CARRY_OVER_DAYS = 2


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


def latest_initial_balances(
    user: User, since: date | None = None, until: date | None = None
) -> dict[str, Income]:
    """El último saldo inicial declarado de cada moneda dentro de [since, until].

    Se ordena por su fecha (``received_at`` o, si no tiene, el día local de su
    ``timestamp``) y, dentro del mismo día, por cuándo se registró.
    """

    tz = _user_tz(user)
    anchors: dict[str, Income] = {}
    for income in Income.objects.filter(user=user).filter(_initial_balance_q()):
        day = _effective_date(income, "received_at", tz)
        if (since and day < since) or (until and day > until):
            continue
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


def _thousands(value: Decimal) -> str:
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _spanish_date(day: date) -> str:
    months = (
        "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
        "septiembre", "octubre", "noviembre", "diciembre",
    )
    return f"{day.day} de {months[day.month - 1]} de {day.year}"


def compute_balance(
    user: User,
    start_date: str | None = None,
    end_date: str | None = None,
    whole_history: bool = False,
) -> dict[str, Any]:
    """Ingresos, gastos y saldo (ingresos − gastos) por moneda en un período.

    - Sin fechas: el MES ACTUAL, del día 1 a hoy (zona del usuario). Es lo que
      la gente entiende por "mi saldo" y la respuesta siempre dice el período,
      para que nadie lo tome por el de toda la vida (decisión de Jaime,
      2026-10-06).
    - Con fechas (YYYY-MM-DD, inclusive): ese período; sin ``start_date``,
      desde el primer registro; sin ``end_date``, hasta hoy.
    - ``whole_history``: todo lo registrado, sin saldos iniciales de por medio.

    Saldo inicial: si el usuario declaró uno ("tengo 1.660.000") DENTRO del
    período, esa moneda se cuenta desde ahí: lo anterior ya está en la cifra
    declarada. En un período que empieza el día 1, una moneda sin saldo inicial
    dentro de él arrastra el declarado en los ``CARRY_OVER_DAYS`` días previos
    (``carried_initial_balance``) y cuenta desde ese día. El 2026-10-05 el ancla se apagaba con solo pasar ``end_date``
    y Tresqu restó gastos de 2025 ya incluidos en el saldo declarado
    (−445.500 COP en vez de 539.000).
    """

    from telegrambot.tools import _filter_by_period

    tz = _user_tz(user)
    today = datetime.now(tz).date()
    default_month = not start_date and not end_date and not whole_history
    if default_month:
        start_date, end_date = today.replace(day=1).isoformat(), today.isoformat()
    if whole_history:
        start_date = end_date = None
    elif start_date and not end_date:
        # "Desde el 1 de octubre" es hasta hoy: así el filtro, el ancla y la
        # etiqueta dicen lo mismo (sin esto entraban movimientos futuros).
        end_date = today.isoformat()

    since = date.fromisoformat(start_date) if start_date else None
    until = date.fromisoformat(end_date) if end_date else None
    anchors = {} if whole_history else latest_initial_balances(user, since, until)
    carried: dict[str, Income] = {}
    if since and since.day == 1 and not whole_history:
        before = latest_initial_balances(
            user, since - timedelta(days=CARRY_OVER_DAYS), since - timedelta(days=1)
        )
        carried = {c: a for c, a in before.items() if c not in anchors}

    expenses = _filter_by_period(
        Expense.objects.filter(user=user), "spent_at", start_date, end_date, user=user
    )
    incomes = _filter_by_period(
        Income.objects.filter(user=user), "received_at", start_date, end_date, user=user
    )

    default_currency = getattr(user, "default_currency", None) or "COP"
    currencies = set(expenses.values_list("currency", flat=True).distinct())
    currencies |= set(incomes.values_list("currency", flat=True).distinct())
    currencies |= set(carried)
    currencies = sorted(currencies or {default_currency}, key=lambda c: (c != default_currency, c))

    by_currency = []
    for currency in currencies:
        exp_qs = expenses.filter(currency=currency)
        inc_qs = incomes.filter(currency=currency)
        anchor = anchors.get(currency) or carried.get(currency)
        if currency in carried:
            # El período de esta moneda empieza el día del saldo arrastrado.
            anchor_day = _effective_date(anchor, "received_at", tz).isoformat()
            exp_qs = _filter_by_period(
                Expense.objects.filter(user=user, currency=currency), "spent_at",
                anchor_day, end_date, user=user,
            )
            inc_qs = _filter_by_period(
                Income.objects.filter(user=user, currency=currency), "received_at",
                anchor_day, end_date, user=user,
            )
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
            "carried_initial_balance": currency in carried,
            # Lo que el agente debe decir de esta moneda, tal cual.
            "counted_label": (
                f"desde tu saldo inicial del {_spanish_date(_effective_date(anchor, 'received_at', tz))} "
                f"({_thousands(anchor.amount)} {currency}), declarado justo antes de empezar el mes, "
                f"hasta el {_spanish_date(until or today)}"
                if currency in carried else None
            ),
            "incomes_total": _money(inc["total"]),
            "incomes_count": inc["count"],
            "expenses_total": _money(exp["total"]),
            "expenses_count": exp["count"],
            "balance": _money(inc["total"] - exp["total"]),
        })

    if whole_history:
        label = "todo lo registrado"
    elif since:
        label = f"del {_spanish_date(since)} al {_spanish_date(until or today)}"
        if default_month:
            label += " (mes actual)"
    else:
        label = f"todo lo registrado hasta el {_spanish_date(until)}"
    return {
        "period": {
            "from": start_date,
            "to": end_date,
            "label": label,
            "current_month": default_month,
        },
        "default_currency": default_currency,
        "by_currency": by_currency,
        "note": (
            "balance = incomes_total - expenses_total, calculado en base de datos. "
            "Repórtalo tal cual y di SIEMPRE el período (period.label) para que el "
            "usuario no crea que es el saldo de toda la vida; si una moneda trae "
            "since_initial_balance, di que cuenta desde ese saldo inicial. Si trae "
            "carried_initial_balance=true, el período de esa moneda es counted_label "
            "(no period.label): díselo con esa fecha y ese monto. No lo recalcules "
            "ni lo combines entre monedas."
        ),
    }
