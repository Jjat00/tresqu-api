"""Regression tests: an expired inference must not read as "no profile".

Bug (2026-09-03, WhatsApp): the user's last auto-inference was 43 days old.
Every chat read path calls ``get_effective_profile(refresh_inference=False)``,
which filtered inferences by a 7-day TTL, so the combiner fell through to
``source="default"`` and the agent answered "no tengo tu perfil de riesgo" to
someone with an ``aggressive``/73 inference on record. Worse, that read then
persisted the default into ``RiskProfile``, downgrading a real profile.

    python manage.py test agents.tests.test_stale_inference
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from agents.effective_profile import get_effective_profile
from agents.models import RiskAssessment, RiskProfile
from agents.tasks import refresh_stale_inferences
from expenses.models import Expense
from income.models import Income
from users.models import User


def _make_user(external_id: str) -> User:
    return User.objects.create(external_id=external_id, platform="WHATSAPP")


def _seed_inference(
    user: User, *, tolerance: str, score: int, age_days: int
) -> RiskAssessment:
    profile, _ = RiskProfile.objects.get_or_create(
        user=user,
        defaults={"tolerance": RiskProfile.MODERATE, "score": 50},
    )
    assessment = RiskAssessment.objects.create(
        profile=profile,
        tolerance=tolerance,
        score=score,
        dimensions={"savings_rate": 60},
        confidence=0.95,
        reason="seeded",
        triggered_by=RiskAssessment.AUTO_INFERENCE,
    )
    # created_at is auto_now_add; rewrite it to simulate an old inference.
    RiskAssessment.objects.filter(pk=assessment.pk).update(
        created_at=timezone.now() - timedelta(days=age_days)
    )
    assessment.refresh_from_db()
    return assessment


class StaleInferenceReadTests(TestCase):
    def test_expired_inference_is_used_instead_of_default(self):
        user = _make_user("wa_stale_read")
        _seed_inference(user, tolerance=RiskProfile.AGGRESSIVE, score=73, age_days=43)

        eff = get_effective_profile(user, refresh_inference=False)

        self.assertEqual(eff.source, "inferred")
        self.assertEqual(eff.tolerance, RiskProfile.AGGRESSIVE)
        self.assertEqual(eff.score, 73)
        self.assertTrue(eff.inferred["stale"])
        self.assertGreaterEqual(eff.inferred["age_days"], 43)
        self.assertIn("infirió", eff.warning)

    def test_fresh_inference_is_not_flagged_stale(self):
        user = _make_user("wa_fresh_read")
        _seed_inference(user, tolerance=RiskProfile.AGGRESSIVE, score=73, age_days=1)

        eff = get_effective_profile(user, refresh_inference=False)

        self.assertEqual(eff.source, "inferred")
        self.assertNotIn("stale", eff.inferred)

    def test_user_with_no_history_still_gets_default(self):
        user = _make_user("wa_empty_read")

        eff = get_effective_profile(user, refresh_inference=False)

        self.assertEqual(eff.source, "default")

    def test_default_read_does_not_downgrade_stored_profile(self):
        user = _make_user("wa_no_downgrade")
        RiskProfile.objects.create(
            user=user,
            tolerance=RiskProfile.AGGRESSIVE,
            score=73,
            confidence=0.95,
        )

        eff = get_effective_profile(user, refresh_inference=False)
        self.assertEqual(eff.source, "default")

        stored = RiskProfile.objects.get(user=user)
        self.assertEqual(stored.tolerance, RiskProfile.AGGRESSIVE)
        self.assertEqual(stored.score, 73)


class RefreshStaleInferencesTaskTests(TestCase):
    def _seed_activity(self, user: User) -> None:
        now = timezone.now()
        for offset in (5, 35, 65):
            Income.objects.create(
                user=user,
                amount=Decimal("1000.00"),
                currency="USD",
                timestamp=now - timedelta(days=offset),
            )
            Expense.objects.create(
                user=user,
                amount=Decimal("400.00"),
                currency="USD",
                timestamp=now - timedelta(days=offset),
            )

    def test_recomputes_only_users_whose_cache_expired(self):
        stale_user = _make_user("wa_task_stale")
        fresh_user = _make_user("wa_task_fresh")
        self._seed_activity(stale_user)
        self._seed_activity(fresh_user)
        _seed_inference(
            stale_user, tolerance=RiskProfile.AGGRESSIVE, score=73, age_days=43
        )
        _seed_inference(
            fresh_user, tolerance=RiskProfile.AGGRESSIVE, score=73, age_days=2
        )

        result = refresh_stale_inferences()

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(
            RiskAssessment.objects.filter(profile__user=stale_user).count(), 2
        )
        self.assertEqual(
            RiskAssessment.objects.filter(profile__user=fresh_user).count(), 1
        )

        eff = get_effective_profile(stale_user, refresh_inference=False)
        self.assertEqual(eff.source, "inferred")
        self.assertNotIn("stale", eff.inferred)

    def test_inactive_users_are_left_alone(self):
        idle_user = _make_user("wa_task_idle")
        _seed_inference(
            idle_user, tolerance=RiskProfile.AGGRESSIVE, score=73, age_days=43
        )

        result = refresh_stale_inferences()

        self.assertEqual(result["refreshed"], 0)
        self.assertEqual(
            RiskAssessment.objects.filter(profile__user=idle_user).count(), 1
        )
