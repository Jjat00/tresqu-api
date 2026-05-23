from django.contrib import admin

from .models import (
    AgentDecision,
    AgentLimits,
    Investment,
    ParsedStatement,
    WallbitAccount,
    WallbitChestLink,
    WallbitTxMirror,
)


@admin.register(WallbitAccount)
class WallbitAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "connected_at", "last_sync_at", "kill_switch_until")
    list_filter = ("status",)
    search_fields = ("user__email",)
    readonly_fields = ("encrypted_api_key", "connected_at")


@admin.register(WallbitTxMirror)
class WallbitTxMirrorAdmin(admin.ModelAdmin):
    list_display = ("wallbit_uuid", "account", "tx_type", "status", "created_at_wallbit")
    list_filter = ("tx_type", "status")
    search_fields = ("wallbit_uuid", "account__user__email")


@admin.register(WallbitChestLink)
class WallbitChestLinkAdmin(admin.ModelAdmin):
    list_display = ("savings_goal", "wallbit_chest_id", "auto_top_up_amount_usd")


@admin.register(Investment)
class InvestmentAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "action", "symbol", "chest_category", "amount_usd", "created_at")
    list_filter = ("kind", "action")
    search_fields = ("user__email", "symbol", "chest_category")


@admin.register(AgentDecision)
class AgentDecisionAdmin(admin.ModelAdmin):
    list_display = ("user", "channel", "requires_confirmation", "confirmed_at", "executed", "created_at")
    list_filter = ("channel", "requires_confirmation", "executed")
    search_fields = ("user__email", "wallbit_tx_uuid")
    readonly_fields = ("created_at",)


@admin.register(AgentLimits)
class AgentLimitsAdmin(admin.ModelAdmin):
    list_display = ("user", "max_trade_usd", "max_daily_move_usd", "require_2step_above_usd")
    search_fields = ("user__email",)


@admin.register(ParsedStatement)
class ParsedStatementAdmin(admin.ModelAdmin):
    list_display = ("user", "source_type", "bank_name", "status", "txs_count", "created_at")
    list_filter = ("source_type", "status")
    search_fields = ("user__email", "bank_name")
