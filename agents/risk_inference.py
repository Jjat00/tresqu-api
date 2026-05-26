"""Automatic risk-tolerance inference from a user's financial context.

This module derives an *inferred* risk profile from observable behavior
(savings rate, income stability, expense stability, holdings appetite),
distinct from the user's *declared* profile coming from the Q&A flow
(see ``risk_profiler_service``). The two are combined elsewhere into an
effective profile.

All dimensions are normalized so that **higher scores indicate higher
capacity / appetite for risk** (i.e. more aggressive):

- ``savings_rate``      — % of income not spent over the last 90 days
- ``income_stability``  — inverse of monthly income coefficient of variation
- ``expense_stability`` — inverse of monthly expense coefficient of variation
- ``holdings_appetite`` — observed allocation to higher-risk instruments

The aggregate ``score`` is a weighted average of those four dimensions
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
    "savings_rate": 0.30,
    "income_stability": 0.25,
    "expense_stability": 0.15,
    "holdings_appetite": 0.30,
}

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


def _coefficient_of_variation(samples: list[float]) -> float | None:
    if len(samples) < 2:
        return None
    mean = statistics.mean(samples)
    if mean == 0:
        return None
    stdev = statistics.pstdev(samples)
    return stdev / mean


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

    income_cv = _coefficient_of_variation(monthly_income)
    expense_cv = _coefficient_of_variation(monthly_expense)

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


def _confidence_for(savings: dict[str, Any], holdings: dict[str, Any]) -> float:
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
    return min(0.95, round(confidence, 2))


def _reason_for(dimensions: dict[str, int], savings: dict[str, Any], holdings: dict[str, Any]) -> str:
    """Short human-readable explanation, Spanish-first."""

    bits: list[str] = []
    if savings["total_income"] > 0:
        rate_pct = round(savings["savings_rate_value"] * 100)
        bits.append(f"tasa de ahorro {rate_pct}% (últimos {INFERENCE_WINDOW_DAYS} días)")
    else:
        bits.append("sin ingresos registrados en la ventana de análisis")

    if savings["income_cv"] is not None:
        bits.append(f"estabilidad de ingreso {dimensions['income_stability']}/100")
    if savings["expense_cv"] is not None:
        bits.append(f"estabilidad de gasto {dimensions['expense_stability']}/100")

    if holdings["has_holdings"]:
        bits.append(
            f"holdings: {round(holdings['aggressive_pct'] * 100)}% en instrumentos volátiles"
        )
    else:
        bits.append("sin inversiones registradas")

    return "Inferencia automática: " + "; ".join(bits) + "."


def compute_inference(user: User) -> InferenceResult:
    """Compute a fresh inference from the user's last 90 days of activity.

    Does NOT persist anything — caller is responsible for storing the result
    if it should be cached. See ``get_or_create_inference``.
    """

    since = timezone.now() - timedelta(days=INFERENCE_WINDOW_DAYS)
    savings = _compute_savings_signals(user, since)
    holdings = _compute_holdings_signals(user)

    dimensions = {
        "savings_rate": _bucket_savings_rate(savings["savings_rate_value"]),
        "income_stability": _stability_from_cv(savings["income_cv"]),
        "expense_stability": _stability_from_cv(savings["expense_cv"]),
        "holdings_appetite": holdings["holdings_appetite_value"],
    }

    raw_score = sum(dimensions[key] * weight for key, weight in WEIGHTS.items())
    score = int(round(raw_score))
    score = max(0, min(100, score))

    return InferenceResult(
        tolerance=_tolerance_for(score),
        score=score,
        dimensions=dimensions,
        confidence=_confidence_for(savings, holdings),
        context_snapshot={
            "window_days": INFERENCE_WINDOW_DAYS,
            "savings": savings,
            "holdings": holdings,
        },
        reason=_reason_for(dimensions, savings, holdings),
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
