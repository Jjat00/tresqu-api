"""Celery tasks for the agents app.

``refresh_stale_inferences`` keeps the automatic risk inference fresh.

The inference is cached as a ``RiskAssessment(triggered_by="auto_inference")``
with a 7-day TTL (``DEFAULT_MAX_AGE_DAYS``). Read paths — the supervisor's
``get_my_risk_profile``, the analyst's ``get_user_risk_profile`` and the
Wallbit risk gate — never recompute it inline: they read the cached row.
Before this task existed the only writer was the dashboard endpoint, so a
user who didn't open the dashboard for a week was told over chat that we had
no profile for them at all. This runs daily and recomputes the inference for
anyone with recent financial activity.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.utils import timezone

from expenses.models import Expense
from income.models import Income
from users.models import User
from wallbit.models import Investment

from .models import RiskAssessment
from .risk_inference import (
    DEFAULT_MAX_AGE_DAYS,
    INFERENCE_WINDOW_DAYS,
    get_or_create_inference,
)

logger = logging.getLogger(__name__)

# Safety valve so one run can never fan out over the whole user base.
REFRESH_BATCH_LIMIT = 500


def _active_user_ids(since) -> set[int]:
    """Users with any financial activity inside the inference window.

    Only these are worth recomputing: for a user with no recent records the
    inference would read as "no income, no savings" and silently downgrade a
    profile that was built from real data. Those keep their last inference,
    flagged as stale by ``effective_profile``.
    """

    ids: set[int] = set()
    ids.update(
        Expense.objects.filter(timestamp__gte=since)
        .values_list("user_id", flat=True)
        .distinct()
    )
    ids.update(
        Income.objects.filter(timestamp__gte=since)
        .values_list("user_id", flat=True)
        .distinct()
    )
    ids.update(
        Investment.objects.filter(created_at__gte=since)
        .values_list("user_id", flat=True)
        .distinct()
    )
    ids.discard(None)
    return ids


@shared_task(name="agents.tasks.refresh_stale_inferences")
def refresh_stale_inferences(
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    limit: int = REFRESH_BATCH_LIMIT,
) -> dict[str, Any]:
    """Recompute the auto-inference for active users whose cache expired."""

    now = timezone.now()
    active_ids = _active_user_ids(now - timedelta(days=INFERENCE_WINDOW_DAYS))
    if not active_ids:
        return {"candidates": 0, "refreshed": 0, "failed": 0}

    fresh_ids = set(
        RiskAssessment.objects.filter(
            triggered_by=RiskAssessment.AUTO_INFERENCE,
            created_at__gte=now - timedelta(days=max_age_days),
            profile__user_id__in=active_ids,
        ).values_list("profile__user_id", flat=True)
    )
    target_ids = sorted(active_ids - fresh_ids)[:limit]

    refreshed = 0
    failed = 0
    for user in User.objects.filter(id__in=target_ids).iterator():
        try:
            get_or_create_inference(user, max_age_days=max_age_days)
            refreshed += 1
        except Exception:  # noqa: BLE001 — one bad user must not stop the batch
            logger.exception("refresh_stale_inferences failed for user=%s", user.id)
            failed += 1

    logger.info(
        "refresh_stale_inferences candidates=%s refreshed=%s failed=%s",
        len(target_ids),
        refreshed,
        failed,
    )
    return {"candidates": len(target_ids), "refreshed": refreshed, "failed": failed}
