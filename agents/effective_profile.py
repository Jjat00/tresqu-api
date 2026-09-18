"""Combine declared (Q&A) and inferred (context) risk signals into an effective profile.

This module is the single source of truth that downstream consumers — the
``RiskProfileCard`` in the dashboard and ``agent_safety.evaluate_decision``
in particular — should read instead of querying ``RiskProfile`` directly.
Reading ``RiskProfile.tolerance`` alone would miss the safety logic that
caps an overconfident self-declaration with the inferred capacity.

The combination rules (see ``_combine``) are:

- Both present and agree                → use declared, boost confidence
- Both present, declared > inferred     → use inferred as safety cap (warning)
- Both present, declared < inferred     → respect user choice (declared)
- Only declared                         → use declared
- Only inferred                         → use inferred (low confidence, prompt user to /perfil)
- Neither                               → moderate default with very low confidence

A ``user_override`` declared profile is treated as a stronger declared
signal — the user has explicitly set their tolerance from the dashboard
and we never silently cap that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from users.models import User

from .models import RiskAssessment, RiskProfile
from .risk_inference import (
    DEFAULT_MAX_AGE_DAYS,
    get_or_create_inference,
    inference_to_dict,
    latest_inference,
)

logger = logging.getLogger(__name__)

# Ordinal ranking used when comparing declared vs inferred tolerances.
_TOLERANCE_ORDER = {
    RiskProfile.CONSERVATIVE: 0,
    RiskProfile.MODERATE: 1,
    RiskProfile.AGGRESSIVE: 2,
}


@dataclass
class EffectiveProfile:
    """The combined view consumers should act on."""

    tolerance: str
    score: int
    confidence: float
    source: str  # "declared" | "inferred" | "agreement" | "safety_cap" | "default"
    declared: dict[str, Any] | None
    inferred: dict[str, Any] | None
    warning: str | None
    user_override: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "tolerance": self.tolerance,
            "score": self.score,
            "confidence": round(self.confidence, 2),
            "source": self.source,
            "declared": self.declared,
            "inferred": self.inferred,
            "warning": self.warning,
            "user_override": self.user_override,
        }


def _latest_declared(user: User) -> RiskAssessment | None:
    """Most recent assessment that represents a *declared* tolerance.

    Includes Q&A outcomes and manual overrides from the dashboard. Excludes
    auto-inference and context-drift records — those are the inferred side.
    """

    return (
        RiskAssessment.objects.filter(
            profile__user=user,
            triggered_by__in=(
                RiskAssessment.CHAT_QA,
                RiskAssessment.MANUAL_OVERRIDE,
                RiskAssessment.USER_REQUEST,
                RiskAssessment.INITIAL,
            ),
        )
        .order_by("-created_at")
        .first()
    )


def _assessment_to_dict(a: RiskAssessment) -> dict[str, Any]:
    return {
        "tolerance": a.tolerance,
        "score": a.score,
        "dimensions": a.dimensions,
        "confidence": a.confidence,
        "reason": a.reason,
        "triggered_by": a.triggered_by,
        "recorded_at": a.created_at.isoformat(),
    }


def _combine(
    declared: RiskAssessment | None,
    inferred: RiskAssessment | None,
    *,
    user_override: bool,
) -> EffectiveProfile:
    declared_dict = _assessment_to_dict(declared) if declared else None
    inferred_dict = _assessment_to_dict(inferred) if inferred else None

    if declared and inferred:
        if declared.tolerance == inferred.tolerance:
            boosted_confidence = min(0.97, max(declared.confidence, inferred.confidence) + 0.10)
            return EffectiveProfile(
                tolerance=declared.tolerance,
                score=int(round((declared.score + inferred.score) / 2)),
                confidence=boosted_confidence,
                source="agreement",
                declared=declared_dict,
                inferred=inferred_dict,
                warning=None,
                user_override=user_override,
            )

        declared_rank = _TOLERANCE_ORDER[declared.tolerance]
        inferred_rank = _TOLERANCE_ORDER[inferred.tolerance]

        if user_override:
            return EffectiveProfile(
                tolerance=declared.tolerance,
                score=declared.score,
                confidence=declared.confidence,
                source="declared",
                declared=declared_dict,
                inferred=inferred_dict,
                warning=(
                    f"Tu perfil declarado es {declared.tolerance} pero el contexto "
                    f"sugiere {inferred.tolerance}. Como configuraste el perfil "
                    "manualmente, respetamos esa elección."
                ),
                user_override=True,
            )

        if declared_rank > inferred_rank:
            return EffectiveProfile(
                tolerance=inferred.tolerance,
                score=inferred.score,
                confidence=max(0.6, inferred.confidence),
                source="safety_cap",
                declared=declared_dict,
                inferred=inferred_dict,
                warning=(
                    f"Tu perfil declarado es {declared.tolerance}, pero tu situación "
                    f"financiera actual sugiere {inferred.tolerance}. Aplicamos el "
                    "perfil más prudente como protección. Puedes ajustarlo manualmente "
                    "desde tu perfil si estás seguro."
                ),
                user_override=False,
            )

        return EffectiveProfile(
            tolerance=declared.tolerance,
            score=declared.score,
            confidence=declared.confidence,
            source="declared",
            declared=declared_dict,
            inferred=inferred_dict,
            warning=(
                f"Declaraste {declared.tolerance}, lo cual es más prudente que lo "
                f"que sugiere tu contexto ({inferred.tolerance}). Respetamos tu elección."
            ),
            user_override=False,
        )

    if declared:
        return EffectiveProfile(
            tolerance=declared.tolerance,
            score=declared.score,
            confidence=declared.confidence,
            source="declared",
            declared=declared_dict,
            inferred=None,
            warning=None,
            user_override=user_override,
        )

    if inferred:
        return EffectiveProfile(
            tolerance=inferred.tolerance,
            score=inferred.score,
            confidence=max(0.4, inferred.confidence - 0.1),
            source="inferred",
            declared=None,
            inferred=inferred_dict,
            warning=(
                "Estamos usando un perfil inferido de tu actividad. Para mayor "
                "precisión, completa el cuestionario escribiendo /perfil."
            ),
            user_override=user_override,
        )

    return EffectiveProfile(
        tolerance=RiskProfile.MODERATE,
        score=50,
        confidence=0.30,
        source="default",
        declared=None,
        inferred=None,
        warning=(
            "Aún no tenemos suficiente información para estimar tu perfil. "
            "Escribe /perfil para evaluarlo."
        ),
        user_override=user_override,
    )


def _mark_stale(eff: EffectiveProfile, inferred: RiskAssessment) -> EffectiveProfile:
    """Flag that the inference backing this profile is past its TTL.

    We prefer an old inference over telling the user they have no profile,
    but the consumer (and the user) must know it wasn't recomputed recently.
    """

    if eff.inferred is None:
        return eff
    age_days = max(0, (timezone.now() - inferred.created_at).days)
    eff.inferred = {**eff.inferred, "stale": True, "age_days": age_days}
    if eff.source in ("inferred", "safety_cap", "agreement"):
        note = (
            f"Este perfil se infirió hace {age_days} días a partir de tu actividad "
            "y puede estar desactualizado; se recalculará pronto."
        )
        eff.warning = f"{eff.warning} {note}" if eff.warning else note
    return eff


def _sync_profile_row(user: User, eff: EffectiveProfile, inferred_dimensions: dict[str, Any] | None) -> None:
    """Mirror the effective profile into ``RiskProfile`` for legacy readers.

    Some existing code (and the existing GET endpoint) reads ``RiskProfile``
    directly. Keep that row aligned with the effective view so they see the
    same answer. Do not clobber ``dimensions`` if the user set them manually
    via the dashboard (``user_override``).

    A ``default`` effective profile carries no information — it just means we
    had nothing to read. Writing it would *downgrade* a real profile to a
    generic moderate/50, so a read never persists that case.
    """

    if eff.source == "default":
        return

    profile = RiskProfile.objects.filter(user=user).first()
    if profile is None:
        return
    profile.tolerance = eff.tolerance
    profile.score = eff.score
    profile.confidence = eff.confidence
    if not profile.user_override and inferred_dimensions:
        profile.dimensions = inferred_dimensions
    profile.derived_from = {"source": eff.source}
    profile.save(update_fields=["tolerance", "score", "confidence", "dimensions", "derived_from", "last_assessed_at"])


def get_effective_profile(
    user: User,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    refresh_inference: bool = True,
) -> EffectiveProfile:
    """Return the user's effective risk profile.

    By default this refreshes the auto-inference if the cached one is older
    than ``max_age_days``. Pass ``refresh_inference=False`` for read-only
    paths (chat tools, safety gates) where recomputing inline would add
    latency — those fall back to the latest inference *whatever its age*
    rather than reporting no profile. Keeping it fresh is the job of
    ``agents.tasks.refresh_stale_inferences``.
    """

    declared = _latest_declared(user)
    stale = False
    if refresh_inference:
        try:
            inferred = get_or_create_inference(user, max_age_days=max_age_days)
        except Exception as exc:
            logger.exception("inference failed for user=%s: %s", user.id, exc)
            inferred = latest_inference(user, max_age_days=None)
            stale = inferred is not None
    else:
        inferred = latest_inference(user, max_age_days=max_age_days)
        if inferred is None:
            # Past its TTL is not the same as non-existent: an old inference
            # still describes this user far better than the generic default.
            inferred = latest_inference(user, max_age_days=None)
            stale = inferred is not None

    profile = RiskProfile.objects.filter(user=user).first()
    user_override = bool(profile and profile.user_override)

    effective = _combine(declared, inferred, user_override=user_override)
    if stale and inferred is not None:
        effective = _mark_stale(effective, inferred)
    _sync_profile_row(
        user,
        effective,
        inferred_dimensions=inferred.dimensions if inferred else None,
    )
    return effective


__all__ = [
    "EffectiveProfile",
    "get_effective_profile",
]
