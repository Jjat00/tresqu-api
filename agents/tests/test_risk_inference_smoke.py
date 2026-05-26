"""Smoke test for the automatic risk-inference module.

Runs against the user's host venv:

    python -m agents.tests.test_risk_inference_smoke

Picks the first user in the DB and exercises:
- ``compute_inference`` — pure function, no DB writes
- ``get_or_create_inference`` — creates a RiskAssessment(auto_inference)
- ``get_or_create_inference`` again — returns the cached one
- ``get_or_create_inference(force=True)`` — recomputes
"""

from __future__ import annotations

import os
import sys
from time import sleep


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _run() -> int:
    from agents.models import RiskAssessment
    from agents.risk_inference import (
        DEFAULT_MAX_AGE_DAYS,
        compute_inference,
        get_or_create_inference,
        latest_inference,
    )
    from users.models import User

    user = User.objects.first()
    if user is None:
        print("[!] no users in DB — run create_superuser first")
        return 1
    print(f"User id={user.id}")

    # Clean any prior inferences for a deterministic run.
    deleted, _ = RiskAssessment.objects.filter(
        profile__user=user, triggered_by=RiskAssessment.AUTO_INFERENCE
    ).delete()
    print(f"[setup] removed {deleted} prior auto-inferences")

    print("\n[1] compute_inference (pure)")
    result = compute_inference(user)
    print(f"    tolerance={result.tolerance} score={result.score} confidence={result.confidence}")
    print(f"    dimensions={result.dimensions}")
    print(f"    reason={result.reason}")
    assert 0 <= result.score <= 100
    assert result.tolerance in ("conservative", "moderate", "aggressive")
    assert 0 <= result.confidence <= 0.95
    assert set(result.dimensions.keys()) == {
        "savings_rate",
        "income_stability",
        "expense_stability",
        "holdings_appetite",
        "liquidity_buffer",
    }
    for dim, val in result.dimensions.items():
        assert 0 <= val <= 100, f"dimension {dim} out of range: {val}"

    print("\n[2] get_or_create_inference (cold) — should create a new RiskAssessment")
    a1 = get_or_create_inference(user, max_age_days=DEFAULT_MAX_AGE_DAYS)
    print(f"    id={a1.id} triggered_by={a1.triggered_by} score={a1.score} tolerance={a1.tolerance}")
    assert a1.triggered_by == RiskAssessment.AUTO_INFERENCE

    print("\n[3] get_or_create_inference (warm) — should return cached row")
    sleep(0.5)
    a2 = get_or_create_inference(user, max_age_days=DEFAULT_MAX_AGE_DAYS)
    assert a2.id == a1.id, f"expected cached row {a1.id}, got new row {a2.id}"
    print(f"    id={a2.id} (matches previous, cache hit)")

    print("\n[4] get_or_create_inference (force=True) — should recompute")
    a3 = get_or_create_inference(user, force=True)
    assert a3.id != a1.id, "force=True should create a new row"
    print(f"    new id={a3.id} (different from cached {a1.id})")

    print("\n[5] latest_inference (read-only)")
    latest = latest_inference(user)
    assert latest is not None and latest.id == a3.id
    print(f"    latest id={latest.id}")

    print("\nINFERENCE SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
