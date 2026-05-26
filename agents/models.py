from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from users.models import User


class RiskProfile(models.Model):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"
    TOLERANCE_CHOICES = [
        (CONSERVATIVE, "Conservative"),
        (MODERATE, "Moderate"),
        (AGGRESSIVE, "Aggressive"),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="risk_profile",
    )
    tolerance = models.CharField(
        max_length=16,
        choices=TOLERANCE_CHOICES,
        default=MODERATE,
    )
    score = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        default=50,
    )
    dimensions = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        default=0.5,
    )
    user_override = models.BooleanField(default=False)
    derived_from = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    last_assessed_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"RiskProfile(user={self.user_id}, tolerance={self.tolerance}, score={self.score})"


class RiskAssessment(models.Model):
    INITIAL = "initial"
    CHAT_QA = "chat_qa"
    AUTO_INFERENCE = "auto_inference"
    CONTEXT_DRIFT = "context_drift"
    USER_REQUEST = "user_request"
    MANUAL_OVERRIDE = "manual_override"
    TRIGGER_CHOICES = [
        (INITIAL, "Initial"),
        (CHAT_QA, "Chat Q&A"),
        (AUTO_INFERENCE, "Auto inference"),
        (CONTEXT_DRIFT, "Context drift"),
        (USER_REQUEST, "User request"),
        (MANUAL_OVERRIDE, "Manual override"),
    ]

    profile = models.ForeignKey(
        RiskProfile,
        on_delete=models.CASCADE,
        related_name="assessments",
    )
    tolerance = models.CharField(
        max_length=16,
        choices=RiskProfile.TOLERANCE_CHOICES,
    )
    score = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    dimensions = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        default=0.5,
    )
    reason = models.TextField(blank=True)
    triggered_by = models.CharField(max_length=24, choices=TRIGGER_CHOICES)
    context_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["profile", "-created_at"]),
            models.Index(fields=["triggered_by"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return (
            f"RiskAssessment(profile={self.profile_id}, "
            f"tolerance={self.tolerance}, score={self.score}, "
            f"trigger={self.triggered_by})"
        )
