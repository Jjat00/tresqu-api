from rest_framework import serializers

from .models import AgentDecision, AgentLimits, WallbitAccount


class WallbitConnectSerializer(serializers.Serializer):
    api_key = serializers.CharField(write_only=True, min_length=10, max_length=512, trim_whitespace=True)
    scope_hint = serializers.CharField(required=False, allow_blank=True, default="read,trade")


class WallbitStatusSerializer(serializers.ModelSerializer):
    connected = serializers.SerializerMethodField()

    class Meta:
        model = WallbitAccount
        fields = (
            "connected",
            "status",
            "scope_hint",
            "connected_at",
            "last_sync_at",
            "kill_switch_until",
            "last_error",
        )
        read_only_fields = fields

    def get_connected(self, obj: WallbitAccount) -> bool:
        return obj.status == WallbitAccount.CONNECTED


class AgentDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentDecision
        fields = (
            "id",
            "channel",
            "user_message",
            "agent_reasoning",
            "tools_called",
            "requires_confirmation",
            "confirmed_at",
            "executed",
            "wallbit_tx_uuid",
            "error",
            "created_at",
        )
        read_only_fields = fields


class AgentLimitsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentLimits
        fields = (
            "max_trade_usd",
            "max_daily_move_usd",
            "allowed_symbols",
            "blocked_symbols",
            "require_2step_above_usd",
        )
