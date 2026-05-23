from rest_framework import serializers

from .models import WallbitAccount


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
