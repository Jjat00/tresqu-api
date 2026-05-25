"""
Agregados mensuales pre-computados para alimentar al LLM con números reales.
El LLM nunca debe recalcular promedios/desviaciones/comparativas a partir
del raw — se equivoca y se inventa. Esta capa hace toda la matemática en
Postgres y devuelve un dict listo para narrar.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date
from decimal import Decimal
from typing import Any

import pytz
from django.db.models import Count, Sum
from django.db.models.functions import ExtractIsoWeekDay
from django.utils import timezone

from expenses.models import Expense
from income.models import Income
from users.models import User

logger = logging.getLogger(__name__)


_DOW_ES = {
    1: "lunes",
    2: "martes",
    3: "miércoles",
    4: "jueves",
    5: "viernes",
    6: "sábado",
    7: "domingo",
}

_MONTH_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _category_name_from_row(row: dict, prefix: str) -> str:
    return (
        row.get(f"{prefix}__name")
        or row.get("category__name")
        or row.get("category_str")
        or "Sin categoría"
    )


def _category_name_from_obj(expense: Expense) -> str:
    if expense.user_expense_category_id:
        try:
            return expense.user_expense_category.name
        except Exception:
            pass
    if expense.category_id:
        try:
            return expense.category.name
        except Exception:
            pass
    return expense.category_str or "Sin categoría"


def compute_monthly_insights(
    user: User,
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    """
    Devuelve un dict pre-agregado del mes (year, month) para el usuario.
    Si year/month vienen None, usa el mes actual en la zona horaria del usuario.
    """
    try:
        user_tz = pytz.timezone(user.timezone)
    except Exception:
        user_tz = pytz.UTC

    now_local = timezone.now().astimezone(user_tz)
    year = year or now_local.year
    month = month or now_local.month

    start_date, end_date = _month_bounds(year, month)
    today_local = now_local.date()
    is_current_month = (year == now_local.year and month == now_local.month)
    effective_end = min(end_date, today_local) if is_current_month else end_date

    currency = getattr(user, "default_currency", "COP")

    expenses_qs = Expense.objects.filter(
        user=user,
        spent_at__gte=start_date,
        spent_at__lte=effective_end,
    )
    incomes_qs = Income.objects.filter(
        user=user,
        received_at__gte=start_date,
        received_at__lte=effective_end,
    )

    total_expenses = _to_float(expenses_qs.aggregate(s=Sum("amount"))["s"])
    total_incomes = _to_float(incomes_qs.aggregate(s=Sum("amount"))["s"])

    # Stats diarios (un punto por día con gasto)
    daily_rows = list(
        expenses_qs
        .values("spent_at")
        .annotate(total=Sum("amount"))
        .order_by("spent_at")
    )
    daily_amounts = [_to_float(r["total"]) for r in daily_rows]
    days_with_expenses = len(daily_amounts)
    avg_daily = sum(daily_amounts) / days_with_expenses if days_with_expenses else 0.0
    if days_with_expenses > 1:
        variance = sum((x - avg_daily) ** 2 for x in daily_amounts) / days_with_expenses
        std_daily = variance ** 0.5
    else:
        std_daily = 0.0

    max_day = max(daily_rows, key=lambda r: _to_float(r["total"])) if daily_rows else None
    max_day_payload = (
        {"date": max_day["spent_at"].isoformat(), "amount": _to_float(max_day["total"])}
        if max_day else None
    )

    # Día de la semana
    dow_rows = list(
        expenses_qs
        .annotate(dow=ExtractIsoWeekDay("spent_at"))
        .values("dow")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("dow")
    )
    day_of_week = [
        {
            "day": _DOW_ES.get(r["dow"], str(r["dow"])),
            "iso_weekday": r["dow"],
            "total": _to_float(r["total"]),
            "count": r["count"],
            "avg": _to_float(r["total"]) / r["count"] if r["count"] else 0.0,
        }
        for r in dow_rows
    ]

    peak_dow_payload = None
    if day_of_week:
        peak_dow = max(day_of_week, key=lambda d: d["total"])
        others = [d["total"] for d in day_of_week if d["iso_weekday"] != peak_dow["iso_weekday"]]
        others_avg = sum(others) / len(others) if others else 0.0
        diff_pct = ((peak_dow["total"] - others_avg) / others_avg * 100) if others_avg else None
        peak_dow_payload = {
            "day": peak_dow["day"],
            "total": peak_dow["total"],
            "vs_other_days_avg_pct": round(diff_pct, 1) if diff_pct is not None else None,
        }

    # Top categorías de gastos del mes actual
    cat_rows = list(
        expenses_qs
        .values("user_expense_category__name", "category__name", "category_str")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    top_categories: list[dict[str, Any]] = []
    cat_totals_current: dict[str, float] = {}
    for r in cat_rows:
        name = _category_name_from_row(r, "user_expense_category")
        total = _to_float(r["total"])
        cat_totals_current[name] = cat_totals_current.get(name, 0.0) + total

    # Re-agrupar por nombre (por si distintos FKs colapsan al mismo nombre)
    grouped: dict[str, dict[str, Any]] = {}
    for r in cat_rows:
        name = _category_name_from_row(r, "user_expense_category")
        total = _to_float(r["total"])
        if name not in grouped:
            grouped[name] = {"name": name, "total": 0.0, "count": 0}
        grouped[name]["total"] += total
        grouped[name]["count"] += r["count"]
    for entry in grouped.values():
        entry["pct_of_total"] = round(entry["total"] / total_expenses * 100, 1) if total_expenses else 0.0
    top_categories = sorted(grouped.values(), key=lambda d: d["total"], reverse=True)

    # Mes anterior por categoría
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_start, prev_end = _month_bounds(prev_year, prev_month)
    prev_rows = list(
        Expense.objects
        .filter(user=user, spent_at__gte=prev_start, spent_at__lte=prev_end)
        .values("user_expense_category__name", "category__name", "category_str")
        .annotate(total=Sum("amount"))
    )
    prev_cat_map: dict[str, float] = {}
    for r in prev_rows:
        name = _category_name_from_row(r, "user_expense_category")
        prev_cat_map[name] = prev_cat_map.get(name, 0.0) + _to_float(r["total"])

    category_growth: list[dict[str, Any]] = []
    for cat in top_categories:
        prev = prev_cat_map.get(cat["name"], 0.0)
        if prev > 0:
            growth_pct = (cat["total"] - prev) / prev * 100
            is_new = False
        elif cat["total"] > 0:
            growth_pct = None
            is_new = True
        else:
            continue
        category_growth.append({
            "name": cat["name"],
            "current": cat["total"],
            "previous": prev,
            "growth_pct": round(growth_pct, 1) if growth_pct is not None else None,
            "is_new_this_month": is_new,
        })

    # Top transacciones individuales
    top_exp_objs = list(
        expenses_qs
        .select_related("user_expense_category", "category")
        .order_by("-amount")[:5]
    )
    top_expenses = [
        {
            "date": e.spent_at.isoformat() if e.spent_at else None,
            "amount": _to_float(e.amount),
            "category": _category_name_from_obj(e),
            "description": (e.description or e.raw_message or "")[:140],
        }
        for e in top_exp_objs
    ]

    # Recurrentes (misma descripción con ≥2 cargos en el mes)
    desc_rows = list(
        expenses_qs
        .exclude(description__isnull=True)
        .exclude(description__exact="")
        .values("description")
        .annotate(count=Count("id"), total=Sum("amount"))
        .filter(count__gte=2)
        .order_by("-count", "-total")[:5]
    )
    recurring = [
        {
            "description": r["description"][:140],
            "count": r["count"],
            "total": _to_float(r["total"]),
            "avg": _to_float(r["total"]) / r["count"],
        }
        for r in desc_rows
    ]

    # Anomalías: tx individual con amount > avg_daily + 2*std (proxy razonable
    # sobre una distribución diaria; evita marcar todo cuando hay 1 outlier).
    if avg_daily > 0 and std_daily > 0:
        threshold = avg_daily + 2 * std_daily
        anomaly_objs = list(
            expenses_qs
            .select_related("user_expense_category", "category")
            .filter(amount__gt=threshold)
            .order_by("-amount")[:5]
        )
        anomalies = [
            {
                "date": e.spent_at.isoformat() if e.spent_at else None,
                "amount": _to_float(e.amount),
                "category": _category_name_from_obj(e),
                "description": (e.description or "")[:140],
                "x_avg_daily": round(_to_float(e.amount) / avg_daily, 1) if avg_daily else None,
            }
            for e in anomaly_objs
        ]
    else:
        anomalies = []

    # Top fuentes de ingreso
    inc_rows = list(
        incomes_qs
        .values("user_income_category__name", "category__name", "category_str")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    )
    inc_grouped: dict[str, dict[str, Any]] = {}
    for r in inc_rows:
        name = r.get("user_income_category__name") or r.get("category__name") or r.get("category_str") or "Sin categoría"
        total = _to_float(r["total"])
        if name not in inc_grouped:
            inc_grouped[name] = {"name": name, "total": 0.0, "count": 0}
        inc_grouped[name]["total"] += total
        inc_grouped[name]["count"] += r["count"]
    for entry in inc_grouped.values():
        entry["pct_of_total"] = round(entry["total"] / total_incomes * 100, 1) if total_incomes else 0.0
    top_income_sources = sorted(inc_grouped.values(), key=lambda d: d["total"], reverse=True)

    return {
        "period": {
            "year": year,
            "month": month,
            "month_name": _MONTH_ES[month],
            "from": start_date.isoformat(),
            "to": effective_end.isoformat(),
            "is_current_month": is_current_month,
            "days_elapsed": (effective_end - start_date).days + 1,
            "days_in_month": (end_date - start_date).days + 1,
        },
        "currency": currency,
        "totals": {
            "expenses": total_expenses,
            "incomes": total_incomes,
            "net": total_incomes - total_expenses,
        },
        "daily_stats": {
            "days_with_expenses": days_with_expenses,
            "avg_daily_expense": round(avg_daily, 2),
            "std_dev_daily_expense": round(std_daily, 2),
            "max_day": max_day_payload,
        },
        "day_of_week": day_of_week,
        "peak_day_of_week": peak_dow_payload,
        "top_categories": top_categories,
        "category_growth_vs_prev_month": category_growth,
        "top_expenses": top_expenses,
        "recurring_merchants": recurring,
        "anomalies": anomalies,
        "top_income_sources": top_income_sources,
    }
