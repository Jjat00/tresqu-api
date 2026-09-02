from rest_framework import serializers

from .models import (
    PENDING_TX_STATUSES,
    AgentDecision,
    AgentLimits,
    Investment,
    WallbitAccount,
)


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
            "status",
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


class InvestmentSerializer(serializers.ModelSerializer):
    wallbit_tx_uuid = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = Investment
        fields = (
            "id",
            "kind",
            "action",
            "symbol",
            "chest_category",
            "amount_usd",
            "shares",
            "wallbit_tx_uuid",
            "status",
            "executed_at",
            "created_at",
        )
        read_only_fields = fields

    def get_wallbit_tx_uuid(self, obj: Investment) -> str | None:
        return obj.wallbit_tx.wallbit_uuid if obj.wallbit_tx_id else None

    def get_status(self, obj: Investment) -> str:
        """'pending' if the linked Wallbit tx hasn't settled yet, else 'executed'."""
        mirror = obj.wallbit_tx
        if mirror and (mirror.status or "").upper() in PENDING_TX_STATUSES:
            return "pending"
        return "executed"


class PendingTradeSerializer(serializers.Serializer):
    symbol = serializers.CharField()
    action = serializers.CharField()
    amount_usd = serializers.DecimalField(max_digits=14, decimal_places=2)
    shares = serializers.DecimalField(
        max_digits=18, decimal_places=8, allow_null=True
    )
    executed_at = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()


class CashBalanceSerializer(serializers.Serializer):
    currency = serializers.CharField()
    amount = serializers.DecimalField(max_digits=18, decimal_places=8)


class PortfolioSummarySerializer(serializers.Serializer):
    total_invested_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    total_withdrawn_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    net_invested_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    current_value_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    pnl_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    pnl_pct = serializers.FloatField()
    holdings_count = serializers.IntegerField()
    cash = CashBalanceSerializer(many=True)
    robo_net_contributed_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    investment_cash_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    last_sync_at = serializers.DateTimeField(allow_null=True)
    pending_trades = PendingTradeSerializer(many=True, read_only=True)
    # True when Wallbit failed / rate-limited us and these live numbers come
    # from the last-good snapshot; ``as_of`` is when they were actually read.
    stale = serializers.BooleanField(read_only=True)
    as_of = serializers.DateTimeField(allow_null=True, read_only=True)


class HoldingSerializer(serializers.Serializer):
    symbol = serializers.CharField()
    name = serializers.CharField(allow_blank=True)
    kind = serializers.CharField(allow_blank=True)
    shares = serializers.DecimalField(max_digits=18, decimal_places=8)
    avg_cost = serializers.DecimalField(max_digits=18, decimal_places=4)
    current_price = serializers.DecimalField(max_digits=18, decimal_places=4)
    cost_basis = serializers.DecimalField(max_digits=18, decimal_places=2)
    market_value = serializers.DecimalField(max_digits=18, decimal_places=2)
    pnl_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    pnl_pct = serializers.FloatField()
    cost_pending = serializers.BooleanField(default=False)


class TimelinePointSerializer(serializers.Serializer):
    date = serializers.DateField()
    invested_total_usd = serializers.DecimalField(max_digits=18, decimal_places=2)


class PnLTimelinePointSerializer(serializers.Serializer):
    # "YYYY-MM-DD" for daily periods, full ISO timestamp for the intraday "1d".
    date = serializers.CharField()
    pnl_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    pnl_pct = serializers.FloatField()
    market_value_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
    invested_total_usd = serializers.DecimalField(max_digits=18, decimal_places=2)
