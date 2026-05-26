"""Smoke test for the effective-profile combiner.

    python -m agents.tests.test_effective_profile

Walks through the combination matrix:
1. No declared + no inferred  → default + warning
2. Inferred only              → inferred + low-confidence warning
3. Declared only              → declared
4. Declared + inferred agree  → agreement (boosted confidence)
5. Declared > inferred        → safety_cap (warning)
6. Declared < inferred        → declared (user-respected)
7. user_override=True overrides safety_cap even when declared > inferred
"""

from __future__ import annotations

import os
import sys


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _run() -> int:
    from agents.effective_profile import get_effective_profile
    from agents.models import RiskAssessment, RiskProfile
    from users.models import User

    user = User.objects.first()
    if user is None:
        print("[!] no users in DB")
        return 1
    print(f"User id={user.id}")

    def reset() -> None:
        RiskAssessment.objects.filter(profile__user=user).delete()
        RiskProfile.objects.filter(user=user).delete()

    def seed_profile(tolerance: str, score: int, user_override: bool = False) -> RiskProfile:
        return RiskProfile.objects.create(
            user=user,
            tolerance=tolerance,
            score=score,
            user_override=user_override,
            confidence=0.7,
        )

    def seed_assessment(profile: RiskProfile, tolerance: str, score: int, triggered_by: str, confidence: float = 0.7) -> RiskAssessment:
        return RiskAssessment.objects.create(
            profile=profile,
            tolerance=tolerance,
            score=score,
            dimensions={},
            confidence=confidence,
            reason=f"seeded for test ({triggered_by})",
            triggered_by=triggered_by,
            context_snapshot={},
        )

    print("\n[1] No declared, no inferred — expect default + warning")
    reset()
    eff = get_effective_profile(user, refresh_inference=False)
    print(f"    source={eff.source} tolerance={eff.tolerance} warning={bool(eff.warning)}")
    assert eff.source in ("default", "inferred"), f"unexpected source {eff.source}"

    print("\n[2] Inferred only — expect inferred + warning to complete /perfil")
    reset()
    profile = seed_profile(RiskProfile.MODERATE, 50)
    seed_assessment(profile, RiskProfile.AGGRESSIVE, 80, RiskAssessment.AUTO_INFERENCE, confidence=0.65)
    eff = get_effective_profile(user, refresh_inference=False)
    print(f"    source={eff.source} tolerance={eff.tolerance} warning={bool(eff.warning)}")
    assert eff.source == "inferred", f"expected inferred, got {eff.source}"
    assert eff.tolerance == RiskProfile.AGGRESSIVE
    assert eff.warning and "/perfil" in eff.warning

    print("\n[3] Declared only — expect declared, no warning")
    reset()
    profile = seed_profile(RiskProfile.MODERATE, 55)
    seed_assessment(profile, RiskProfile.MODERATE, 55, RiskAssessment.CHAT_QA, confidence=0.8)
    eff = get_effective_profile(user, refresh_inference=False)
    print(f"    source={eff.source} tolerance={eff.tolerance}")
    assert eff.source == "declared"
    assert eff.tolerance == RiskProfile.MODERATE
    assert eff.warning is None

    print("\n[4] Agreement — expect agreement + boosted confidence")
    reset()
    profile = seed_profile(RiskProfile.MODERATE, 55)
    seed_assessment(profile, RiskProfile.MODERATE, 55, RiskAssessment.CHAT_QA, confidence=0.7)
    seed_assessment(profile, RiskProfile.MODERATE, 60, RiskAssessment.AUTO_INFERENCE, confidence=0.65)
    eff = get_effective_profile(user, refresh_inference=False)
    print(f"    source={eff.source} tolerance={eff.tolerance} confidence={eff.confidence}")
    assert eff.source == "agreement"
    assert eff.confidence > 0.7, "expected boosted confidence on agreement"

    print("\n[5] Declared > inferred — expect safety_cap (use inferred)")
    reset()
    profile = seed_profile(RiskProfile.AGGRESSIVE, 80)
    seed_assessment(profile, RiskProfile.AGGRESSIVE, 80, RiskAssessment.CHAT_QA, confidence=0.8)
    seed_assessment(profile, RiskProfile.CONSERVATIVE, 25, RiskAssessment.AUTO_INFERENCE, confidence=0.7)
    eff = get_effective_profile(user, refresh_inference=False)
    print(f"    source={eff.source} tolerance={eff.tolerance} warning_has_mismatch={'situación financiera' in (eff.warning or '')}")
    assert eff.source == "safety_cap"
    assert eff.tolerance == RiskProfile.CONSERVATIVE
    assert eff.warning is not None

    print("\n[6] Declared < inferred — expect declared (user-respected) with note")
    reset()
    profile = seed_profile(RiskProfile.CONSERVATIVE, 25)
    seed_assessment(profile, RiskProfile.CONSERVATIVE, 25, RiskAssessment.CHAT_QA, confidence=0.8)
    seed_assessment(profile, RiskProfile.AGGRESSIVE, 80, RiskAssessment.AUTO_INFERENCE, confidence=0.7)
    eff = get_effective_profile(user, refresh_inference=False)
    print(f"    source={eff.source} tolerance={eff.tolerance} warning_present={bool(eff.warning)}")
    assert eff.source == "declared"
    assert eff.tolerance == RiskProfile.CONSERVATIVE
    assert eff.warning is not None

    print("\n[7] user_override=True overrides safety_cap")
    reset()
    profile = seed_profile(RiskProfile.AGGRESSIVE, 80, user_override=True)
    seed_assessment(profile, RiskProfile.AGGRESSIVE, 80, RiskAssessment.MANUAL_OVERRIDE, confidence=0.9)
    seed_assessment(profile, RiskProfile.CONSERVATIVE, 25, RiskAssessment.AUTO_INFERENCE, confidence=0.7)
    eff = get_effective_profile(user, refresh_inference=False)
    print(f"    source={eff.source} tolerance={eff.tolerance} override={eff.user_override}")
    assert eff.source == "declared"
    assert eff.tolerance == RiskProfile.AGGRESSIVE
    assert eff.user_override is True

    print("\nCleanup")
    reset()
    print("\nEFFECTIVE PROFILE SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
