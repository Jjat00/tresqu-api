"""Smoke test for the risk-profile gate in agent_safety.

Runs against the host venv (non-destructive — all DB writes happen inside
``transaction.atomic`` blocks that are rolled back explicitly):

    python -m wallbit.tests.test_risk_gate_smoke

Cases covered:
- BUY with aggressive declared profile → pass through, no warning
- BUY with conservative declared profile → blocked-through-to-warning + extra two_step
- BUY with moderate profile under threshold → pass through
- BUY with moderate profile over threshold → warning + extra two_step
- SELL with conservative profile → pass through (only BUY is gated)
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from decimal import Decimal


class _Rollback(Exception):
    """Sentinel used to force ``transaction.atomic`` to roll back."""


@contextmanager
def _rolled_back_atomic():
    from django.db import transaction

    try:
        with transaction.atomic():
            yield
            raise _Rollback
    except _Rollback:
        pass


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _make_user(username: str):
    from users.models import User

    return User.objects.create(
        external_id=f"test-{username}",
        platform="test",
        username=username,
        first_name="Test",
    )


def _force_tolerance(user, tolerance: str) -> None:
    """Create a declared RiskAssessment + RiskProfile row pinned to a tolerance."""
    from agents.models import RiskAssessment, RiskProfile

    score_by_tol = {"conservative": 25, "moderate": 50, "aggressive": 80}
    profile, _ = RiskProfile.objects.get_or_create(
        user=user,
        defaults={"tolerance": tolerance, "score": score_by_tol[tolerance]},
    )
    profile.tolerance = tolerance
    profile.score = score_by_tol[tolerance]
    profile.user_override = True
    profile.save()

    RiskAssessment.objects.create(
        profile=profile,
        tolerance=tolerance,
        score=score_by_tol[tolerance],
        confidence=0.9,
        dimensions={},
        reason="forced for smoke test",
        triggered_by=RiskAssessment.MANUAL_OVERRIDE,
    )


def _run() -> int:
    from wallbit.agent_safety import evaluate_risk_profile_gate
    from wallbit.models import AgentLimits

    failures: list[str] = []

    def _expect(condition: bool, label: str) -> None:
        marker = "OK" if condition else "FAIL"
        print(f"    [{marker}] {label}")
        if not condition:
            failures.append(label)

    print("\n[1] BUY + aggressive → pass through")
    with _rolled_back_atomic():
        user = _make_user("risk_gate_aggressive")
        _force_tolerance(user, "aggressive")
        gate = evaluate_risk_profile_gate(user, action="BUY", amount_usd=Decimal("100"))
        _expect(gate.pass_through is True, "pass_through is True")
        _expect(gate.warning == "", "warning is empty")
        _expect(gate.extra_two_step is False, "extra_two_step is False")
        _expect(
            gate.snapshot is not None and gate.snapshot.get("tolerance") == "aggressive",
            "snapshot.tolerance == aggressive",
        )

    print("\n[2] BUY + conservative → warning + extra_two_step")
    with _rolled_back_atomic():
        user = _make_user("risk_gate_conservative")
        _force_tolerance(user, "conservative")
        gate = evaluate_risk_profile_gate(user, action="BUY", amount_usd=Decimal("100"))
        _expect(gate.pass_through is False, "pass_through is False")
        _expect("conservador" in gate.warning.lower(), "warning mentions conservador")
        _expect(gate.extra_two_step is True, "extra_two_step is True")

    print("\n[3] BUY + moderate + small amount → pass through")
    with _rolled_back_atomic():
        user = _make_user("risk_gate_moderate_small")
        _force_tolerance(user, "moderate")
        limits, _ = AgentLimits.objects.get_or_create(user=user)
        # default max_trade_usd=500, threshold=250. Under threshold:
        gate = evaluate_risk_profile_gate(user, action="BUY", amount_usd=Decimal("100"))
        _expect(gate.pass_through is True, "pass_through is True")
        _expect(gate.warning == "", "no warning")

    print("\n[4] BUY + moderate + large amount → warning + extra_two_step")
    with _rolled_back_atomic():
        user = _make_user("risk_gate_moderate_large")
        _force_tolerance(user, "moderate")
        AgentLimits.objects.get_or_create(user=user)
        # default max_trade_usd=500, threshold=250. 300 > 250:
        gate = evaluate_risk_profile_gate(user, action="BUY", amount_usd=Decimal("300"))
        _expect(gate.pass_through is False, "pass_through is False")
        _expect(gate.extra_two_step is True, "extra_two_step is True")
        _expect("moderado" in gate.warning.lower(), "warning mentions moderado")

    print("\n[5] SELL + conservative → pass through (gate is BUY-only)")
    with _rolled_back_atomic():
        user = _make_user("risk_gate_sell_only")
        _force_tolerance(user, "conservative")
        gate = evaluate_risk_profile_gate(user, action="SELL", amount_usd=Decimal("100"))
        _expect(gate.pass_through is True, "pass_through True for SELL")
        _expect(gate.warning == "", "no warning for SELL")

    if failures:
        print(f"\nRISK GATE SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\nRISK GATE SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
