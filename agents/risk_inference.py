"""Automatic risk-tolerance inference from a user's financial context.

This module derives an *inferred* risk profile from observable behavior
(savings rate, income stability, expense stability, holdings appetite,
liquidity buffer), distinct from the user's *declared* profile coming
from the Q&A flow. The two are combined elsewhere into an effective
profile.

All dimensions are normalized so that **higher scores indicate higher
capacity / appetite for risk** (i.e. more aggressive):

- ``savings_rate``      — % of income not spent over the last 90 days
- ``income_stability``  — penalizes only months *below* the mean (downside
                          deviation, Sortino-style): a bonus month does not
                          count against you
- ``expense_stability`` — penalizes only months *above* the mean (upside
                          deviation): cheap months don't count against you
- ``holdings_appetite`` — observed allocation to higher-risk instruments
- ``liquidity_buffer``  — months of expenses covered by net accumulated
                          savings (lifetime income minus lifetime expenses).
                          NOTE: derived from our records, not an actual
                          bank balance.

The aggregate ``score`` is a weighted average of those five dimensions
and maps to a categorical tolerance (conservative / moderate / aggressive).

Inferences are persisted as ``RiskAssessment(triggered_by="auto_inference")``
records. ``get_or_create_inference`` returns the most recent one if it is
younger than ``max_age_days`` (default 7), otherwise it computes a fresh
one.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from expenses.models import Expense
from income.models import Income
from users.models import User
from wallbit.models import Investment

from .models import RiskAssessment, RiskProfile

logger = logging.getLogger(__name__)

INFERENCE_WINDOW_DAYS = 90
DEFAULT_MAX_AGE_DAYS = 7

# Dimension weights for the aggregate score. Must sum to 1.0.
WEIGHTS = {
    "savings_rate": 0.25,
    "income_stability": 0.20,
    "expense_stability": 0.10,
    "holdings_appetite": 0.25,
    "liquidity_buffer": 0.20,
}

# Need at least this many days of records before the buffer signal becomes
# meaningful — otherwise it just reflects whatever happened in the last few
# weeks and would penalize/reward newcomers unfairly.
BUFFER_MIN_DAYS = 60

CONSERVATIVE_MAX = 35
MODERATE_MAX = 65


@dataclass
class InferenceResult:
    """Outcome of an automatic inference run.

    All ``dimensions`` are in 0-100 (higher = more risk capacity/appetite).
    ``confidence`` reflects how much signal was available — sparse data yields
    a lower number even if the score itself is decisive.
    """

    tolerance: str
    score: int
    dimensions: dict[str, int]
    confidence: float
    context_snapshot: dict[str, Any]
    reason: str


# ---------------------------------------------------------------------------
# Dimension calculators
# ---------------------------------------------------------------------------


def _bucket_savings_rate(rate: float) -> int:
    """Map savings_rate (-inf, +inf) into 0-100 capacity score.

    Negative or zero savings → no buffer → minimum capacity.
    The brackets are calibrated for LATAM personal finance where 20-35%
    is considered healthy and 50%+ is exceptional.
    """

    if rate <= 0:
        return 0
    if rate < 0.10:
        return 20
    if rate < 0.20:
        return 40
    if rate < 0.35:
        return 60
    if rate < 0.50:
        return 80
    return 100


def _stability_from_cv(cv: float | None) -> int:
    """Map a coefficient-of-variation into a stability score (higher = stabler).

    Returns 50 (neutral) if there isn't enough data to compute a CV.
    """

    if cv is None:
        return 50
    if cv < 0.05:
        return 100
    if cv < 0.15:
        return 75
    if cv < 0.30:
        return 50
    if cv < 0.50:
        return 25
    return 0


def _monthly_totals(values: list[tuple[Any, Decimal]]) -> list[float]:
    """Aggregate ``(timestamp, amount)`` tuples into per-month sums.

    Months with zero activity are excluded — a user without income for a month
    isn't "stable at zero", they have a gap. The caller decides what to do
    when there are too few points.
    """

    buckets: dict[tuple[int, int], float] = defaultdict(float)
    for ts, amount in values:
        if ts is None or amount is None:
            continue
        buckets[(ts.year, ts.month)] += float(amount)
    return [v for v in buckets.values() if v > 0]


def _downside_cv(samples: list[float], *, direction: str) -> float | None:
    """Semi-deviation / mean. Only counts the 'bad' side.

    ``direction="below"`` → only months *below* the mean count as instability
    (used for income: a bonus month is good, not unstable).
    ``direction="above"`` → only months *above* the mean count as instability
    (used for expenses: a cheap month is good, not unstable).

    Returns ``None`` with fewer than 2 samples or a non-positive mean,
    matching the previous CV contract so ``_stability_from_cv(None)`` keeps
    yielding the neutral 50 score.
    """

    if len(samples) < 2:
        return None
    mean = statistics.mean(samples)
    if mean <= 0:
        return None
    if direction == "below":
        bad = [s for s in samples if s < mean]
    elif direction == "above":
        bad = [s for s in samples if s > mean]
    else:
        raise ValueError(f"unknown direction: {direction!r}")
    if not bad:
        # All months landed on the favorable side of the mean → perfect stability.
        return 0.0
    # Mean squared deviation over *all* samples, but summing only the bad side.
    # Dividing by len(samples) (not len(bad)) keeps the scale comparable to the
    # full CV when downside dominates, and gives a softer signal when bad
    # months are rare.
    semi_var = sum((s - mean) ** 2 for s in bad) / len(samples)
    return (semi_var ** 0.5) / mean


def _compute_savings_signals(user: User, since) -> dict[str, Any]:
    incomes = list(
        Income.objects.filter(user=user, timestamp__gte=since).values_list(
            "timestamp", "amount"
        )
    )
    expenses = list(
        Expense.objects.filter(user=user, timestamp__gte=since).values_list(
            "timestamp", "amount"
        )
    )

    total_income = sum((amt for _, amt in incomes), Decimal(0))
    total_expense = sum((amt for _, amt in expenses), Decimal(0))

    rate = 0.0
    if total_income > 0:
        rate = float((total_income - total_expense) / total_income)

    monthly_income = _monthly_totals(incomes)
    monthly_expense = _monthly_totals(expenses)

    income_cv = _downside_cv(monthly_income, direction="below")
    expense_cv = _downside_cv(monthly_expense, direction="above")

    return {
        "savings_rate_value": rate,
        "income_cv": income_cv,
        "expense_cv": expense_cv,
        "income_count": len(incomes),
        "expense_count": len(expenses),
        "income_months": len(monthly_income),
        "expense_months": len(monthly_expense),
        "total_income": float(total_income),
        "total_expense": float(total_expense),
    }


def _bucket_buffer_months(months: float) -> int:
    """Map months-of-expenses buffer into a 0-100 capacity score.

    Anchors:
    - 0 months or net debt → no cushion
    - 1-3 months           → minimal cushion (LATAM survival)
    - 3-6 months           → healthy (Dave Ramsey range)
    - 6-12 months          → strong
    - 12+ months           → exceptional
    """

    if months < 0:
        return 0
    if months < 1:
        return 15
    if months < 3:
        return 40
    if months < 6:
        return 70
    if months < 12:
        return 90
    return 100


def _compute_buffer_signals(user: User, savings: dict[str, Any]) -> dict[str, Any]:
    """Net accumulated savings expressed as months of average expense covered.

    Sums income and expense across the user's *entire* recorded history (not
    just the 90-day window) and divides by the recent monthly expense
    average. This is **not** the user's real bank balance — it is what we
    can derive from their records, and we flag it as such in the UI.

    Returns a neutral 50 with ``has_buffer=False`` when there isn't enough
    history (< ``BUFFER_MIN_DAYS`` days) so newcomers don't get penalized
    or rewarded based on noise.
    """

    income_rows = list(
        Income.objects.filter(user=user).values_list("timestamp", "amount")
    )
    expense_rows = list(
        Expense.objects.filter(user=user).values_list("timestamp", "amount")
    )

    total_income_all = sum((amt for _, amt in income_rows if amt is not None), Decimal(0))
    total_expense_all = sum((amt for _, amt in expense_rows if amt is not None), Decimal(0))
    net_accumulated = float(total_income_all - total_expense_all)

    timestamps = [ts for ts, _ in income_rows + expense_rows if ts is not None]
    if timestamps:
        earliest = min(timestamps)
        days_with_records = max(1, (timezone.now() - earliest).days)
    else:
        days_with_records = 0

    monthly_avg_expense = savings["total_expense"] / 3.0 if savings["total_expense"] > 0 else 0.0

    if days_with_records < BUFFER_MIN_DAYS or monthly_avg_expense <= 0:
        return {
            "liquidity_buffer_value": 50,
            "has_buffer": False,
            "net_accumulated": round(net_accumulated, 2),
            "monthly_avg_expense": round(monthly_avg_expense, 2),
            "months_of_buffer": None,
            "days_with_records": days_with_records,
        }

    months_of_buffer = net_accumulated / monthly_avg_expense

    return {
        "liquidity_buffer_value": _bucket_buffer_months(months_of_buffer),
        "has_buffer": True,
        "net_accumulated": round(net_accumulated, 2),
        "monthly_avg_expense": round(monthly_avg_expense, 2),
        "months_of_buffer": round(months_of_buffer, 1),
        "days_with_records": days_with_records,
    }


# Aggressive instrument kinds (price volatility / drawdown risk).
_AGGRESSIVE_KINDS = {Investment.STOCK, Investment.ETF}
# Defensive instrument kinds (lower volatility).
_DEFENSIVE_KINDS = {Investment.BOND, Investment.ROBO, Investment.CHEST}


def _compute_holdings_signals(user: User) -> dict[str, Any]:
    """Holdings appetite based on net invested USD per instrument kind.

    Net = sum(BUY) - sum(SELL). Negative nets are clamped to zero (the
    user is fully out of that bucket). Returns score 0-100 where 100
    means the user's actual money is in higher-volatility instruments.
    """

    qs = Investment.objects.filter(user=user)
    if not qs.exists():
        return {"holdings_appetite_value": 50, "has_holdings": False, "totals": {}}

    nets: dict[str, float] = defaultdict(float)
    for inv in qs.only("kind", "action", "amount_usd"):
        sign = 1 if inv.action == Investment.BUY else -1 if inv.action == Investment.SELL else 0
        if sign == 0:
            continue
        nets[inv.kind] += sign * float(inv.amount_usd or 0)

    nets = {k: max(0.0, v) for k, v in nets.items()}
    total = sum(nets.values())
    if total <= 0:
        return {"holdings_appetite_value": 50, "has_holdings": False, "totals": dict(nets)}

    aggressive = sum(v for k, v in nets.items() if k in _AGGRESSIVE_KINDS)
    defensive = sum(v for k, v in nets.items() if k in _DEFENSIVE_KINDS)
    aggressive_pct = aggressive / total if total else 0
    defensive_pct = defensive / total if total else 0

    if aggressive_pct >= 0.70:
        score = 100
    elif aggressive_pct >= 0.50:
        score = 80
    elif aggressive_pct >= 0.30:
        score = 60
    elif defensive_pct >= 0.70:
        score = 10
    elif defensive_pct >= 0.50:
        score = 30
    else:
        score = 50

    return {
        "holdings_appetite_value": score,
        "has_holdings": True,
        "totals": dict(nets),
        "aggressive_pct": round(aggressive_pct, 3),
        "defensive_pct": round(defensive_pct, 3),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _tolerance_for(score: int) -> str:
    if score <= CONSERVATIVE_MAX:
        return RiskProfile.CONSERVATIVE
    if score <= MODERATE_MAX:
        return RiskProfile.MODERATE
    return RiskProfile.AGGRESSIVE


def _confidence_for(
    savings: dict[str, Any],
    holdings: dict[str, Any],
    buffer: dict[str, Any],
) -> float:
    confidence = 0.40
    txs = savings["income_count"] + savings["expense_count"]
    if txs >= 10:
        confidence += 0.15
    if txs >= 30:
        confidence += 0.10
    if savings["income_months"] >= 2 and savings["expense_months"] >= 2:
        confidence += 0.10
    if savings["income_months"] >= 3 and savings["expense_months"] >= 3:
        confidence += 0.10
    if holdings["has_holdings"]:
        confidence += 0.10
    if buffer["has_buffer"]:
        confidence += 0.05
    return min(0.95, round(confidence, 2))


def _reason_for(
    dimensions: dict[str, int],
    savings: dict[str, Any],
    holdings: dict[str, Any],
    buffer: dict[str, Any],
) -> str:
    """Short human-readable explanation, Spanish-first."""

    bits: list[str] = []
    if savings["total_income"] > 0:
        rate_pct = round(savings["savings_rate_value"] * 100)
        bits.append(f"tasa de ahorro {rate_pct}% (últimos {INFERENCE_WINDOW_DAYS} días)")
    else:
        bits.append("sin ingresos registrados en la ventana de análisis")

    if savings["income_cv"] is not None:
        bits.append(
            f"previsibilidad de ingreso {dimensions['income_stability']}/100 (solo cuenta meses bajos)"
        )
    if savings["expense_cv"] is not None:
        bits.append(
            f"previsibilidad de gasto {dimensions['expense_stability']}/100 (solo cuenta meses altos)"
        )

    if holdings["has_holdings"]:
        bits.append(
            f"holdings: {round(holdings['aggressive_pct'] * 100)}% en instrumentos volátiles"
        )
    else:
        bits.append("sin inversiones registradas")

    if buffer["has_buffer"]:
        bits.append(
            f"reserva acumulada ≈ {buffer['months_of_buffer']} meses de gasto"
        )
    else:
        bits.append("aún sin suficiente historial para evaluar la reserva acumulada")

    return "Inferencia automática: " + "; ".join(bits) + "."


def compute_inference(user: User) -> InferenceResult:
    """Compute a fresh inference from the user's last 90 days of activity.

    Does NOT persist anything — caller is responsible for storing the result
    if it should be cached. See ``get_or_create_inference``.
    """

    since = timezone.now() - timedelta(days=INFERENCE_WINDOW_DAYS)
    savings = _compute_savings_signals(user, since)
    holdings = _compute_holdings_signals(user)
    buffer = _compute_buffer_signals(user, savings)

    dimensions = {
        "savings_rate": _bucket_savings_rate(savings["savings_rate_value"]),
        "income_stability": _stability_from_cv(savings["income_cv"]),
        "expense_stability": _stability_from_cv(savings["expense_cv"]),
        "holdings_appetite": holdings["holdings_appetite_value"],
        "liquidity_buffer": buffer["liquidity_buffer_value"],
    }

    raw_score = sum(dimensions[key] * weight for key, weight in WEIGHTS.items())
    score = int(round(raw_score))
    score = max(0, min(100, score))

    return InferenceResult(
        tolerance=_tolerance_for(score),
        score=score,
        dimensions=dimensions,
        confidence=_confidence_for(savings, holdings, buffer),
        context_snapshot={
            "window_days": INFERENCE_WINDOW_DAYS,
            "savings": savings,
            "holdings": holdings,
            "buffer": buffer,
        },
        reason=_reason_for(dimensions, savings, holdings, buffer),
    )


def _get_or_create_profile(user: User) -> RiskProfile:
    profile, _ = RiskProfile.objects.get_or_create(
        user=user,
        defaults={
            "tolerance": RiskProfile.MODERATE,
            "score": 50,
            "confidence": 0.5,
            "dimensions": {},
            "derived_from": {},
        },
    )
    return profile


def latest_inference(user: User, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> RiskAssessment | None:
    """Return the most recent cached auto-inference if it's still fresh."""

    cutoff = timezone.now() - timedelta(days=max_age_days)
    return (
        RiskAssessment.objects.filter(
            profile__user=user,
            triggered_by=RiskAssessment.AUTO_INFERENCE,
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
        .first()
    )


def get_or_create_inference(
    user: User,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    force: bool = False,
) -> RiskAssessment:
    """Return a fresh auto-inference, computing one if no recent cached value exists.

    ``force=True`` always recomputes regardless of cache. Always returns a
    ``RiskAssessment`` row that the caller can read; if computation fails an
    exception propagates.
    """

    if not force:
        cached = latest_inference(user, max_age_days=max_age_days)
        if cached:
            return cached

    result = compute_inference(user)
    profile = _get_or_create_profile(user)
    assessment = RiskAssessment.objects.create(
        profile=profile,
        tolerance=result.tolerance,
        score=result.score,
        dimensions=result.dimensions,
        confidence=result.confidence,
        reason=result.reason,
        triggered_by=RiskAssessment.AUTO_INFERENCE,
        context_snapshot=result.context_snapshot,
    )
    logger.info(
        "auto_inference user=%s score=%s tolerance=%s confidence=%s",
        user.id,
        result.score,
        result.tolerance,
        result.confidence,
    )
    return assessment


def inference_to_dict(assessment: RiskAssessment) -> dict[str, Any]:
    """Serialize an inference assessment to a JSON-friendly dict."""

    return {
        "tolerance": assessment.tolerance,
        "score": assessment.score,
        "dimensions": assessment.dimensions,
        "confidence": assessment.confidence,
        "reason": assessment.reason,
        "computed_at": assessment.created_at.isoformat(),
    }


__all__ = [
    "InferenceResult",
    "compute_inference",
    "get_or_create_inference",
    "inference_to_dict",
    "latest_inference",
    "INFERENCE_WINDOW_DAYS",
    "DEFAULT_MAX_AGE_DAYS",
]
