from django.contrib import admin

from .models import RiskAssessment, RiskProfile


@admin.register(RiskProfile)
class RiskProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "tolerance",
        "score",
        "confidence",
        "user_override",
        "last_assessed_at",
    )
    list_filter = ("tolerance", "user_override")
    search_fields = ("user__email",)
    readonly_fields = ("last_assessed_at", "created_at")


@admin.register(RiskAssessment)
class RiskAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "profile",
        "tolerance",
        "score",
        "confidence",
        "triggered_by",
        "created_at",
    )
    list_filter = ("tolerance", "triggered_by")
    search_fields = ("profile__user__email",)
    readonly_fields = ("created_at",)
