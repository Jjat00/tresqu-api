from rest_framework import serializers

from .models import RiskAssessment, RiskProfile


VALID_DIMENSION_KEYS = {
    "time_horizon",
    "loss_tolerance",
    "liquidity_need",
    "knowledge_level",
}


def _validate_dimensions(value):
    if not isinstance(value, dict):
        raise serializers.ValidationError("dimensions debe ser un objeto.")
    unknown = set(value.keys()) - VALID_DIMENSION_KEYS
    if unknown:
        raise serializers.ValidationError(
            f"Dimensiones desconocidas: {sorted(unknown)}. "
            f"Permitidas: {sorted(VALID_DIMENSION_KEYS)}."
        )
    for key, score in value.items():
        if not isinstance(score, (int, float)) or not (0 <= score <= 100):
            raise serializers.ValidationError(
                f"dimensions.{key} debe ser numérico entre 0 y 100."
            )
    return value


class RiskProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskProfile
        fields = (
            "tolerance",
            "score",
            "dimensions",
            "confidence",
            "user_override",
            "notes",
            "last_assessed_at",
            "created_at",
        )
        read_only_fields = (
            "confidence",
            "user_override",
            "last_assessed_at",
            "created_at",
        )

    def validate_dimensions(self, value):
        return _validate_dimensions(value)


class RiskProfileUpdateSerializer(serializers.ModelSerializer):
    """Used when the user manually overrides their profile from the UI."""

    class Meta:
        model = RiskProfile
        fields = ("tolerance", "score", "dimensions", "notes")

    def validate_dimensions(self, value):
        return _validate_dimensions(value)


class RiskAssessmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskAssessment
        fields = (
            "id",
            "tolerance",
            "score",
            "dimensions",
            "confidence",
            "reason",
            "triggered_by",
            "created_at",
        )
        read_only_fields = fields
