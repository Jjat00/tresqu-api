"""Pre-flight safety checks and decision lifecycle for Wallbit write tools.

Every write path must:
1. Resolve the connected WallbitAccount (or refuse)
2. Check kill_switch
3. Evaluate AgentLimits (trade cap, daily cap, allow/block lists)
4. Persist an AgentDecision(requires_confirmation=True) with the preview
5. Wait for /api/wallbit/agent/confirm/{id}/ to execute

The two-step flag is informational for the bot layer — limits above
require_2step_above_usd warrant a stronger confirmation prompt but the
decision row is identical.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from users.models import User

from .models import AgentDecision, AgentLimits, Investment, WallbitAccount


class SafetyError(Exception):
    """Base class for pre-flight rejections."""

    code: str = "safety_error"


class AccountNotConnected(SafetyError):
    code = "wallbit_not_connected"


class KillSwitchActive(SafetyError):
    code = "kill_switch_active"


class LimitViolation(SafetyError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class LimitsCheck:
    ok: bool
    two_step_required: bool = False
    code: str = ""
    reason: str = ""


def get_account_or_raise(user: User) -> WallbitAccount:
    account = WallbitAccount.objects.filter(user=user).first()
    if account is None:
        raise AccountNotConnected("Wallbit account is not connected.")
    if account.status != WallbitAccount.CONNECTED:
        raise AccountNotConnected(
            f"Wallbit account is {account.status}, reconnect from the dashboard."
        )
    return account


def check_kill_switch(account: WallbitAccount) -> None:
    if account.kill_switch_until and account.kill_switch_until > timezone.now():
        raise KillSwitchActive(
            f"Wallbit kill switch active until {account.kill_switch_until.isoformat()}."
        )


def _limits_for(user: User) -> AgentLimits:
    limits, _ = AgentLimits.objects.get_or_create(user=user)
    return limits


def _to_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _daily_move_total_usd(user: User) -> Decimal:
    since = timezone.now() - timedelta(hours=24)
    total = Investment.objects.filter(user=user, created_at__gte=since).aggregate(
        s=Sum("amount_usd")
    )["s"]
    return Decimal(total or 0)


def evaluate_trade_limits(
    user: User, symbol: str, amount_usd: Decimal | float | int
) -> LimitsCheck:
    amount = _to_decimal(amount_usd)
    limits = _limits_for(user)
    symbol_upper = (symbol or "").upper()

    blocked = {s.upper() for s in (limits.blocked_symbols or [])}
    if symbol_upper and symbol_upper in blocked:
        return LimitsCheck(
            ok=False,
            code="symbol_blocked",
            reason=f"{symbol_upper} está en tu lista de símbolos bloqueados.",
        )

    allowed = {s.upper() for s in (limits.allowed_symbols or [])}
    if allowed and symbol_upper and symbol_upper not in allowed:
        return LimitsCheck(
            ok=False,
            code="symbol_not_allowed",
            reason=f"{symbol_upper} no está en tu lista de símbolos permitidos.",
        )

    if amount > limits.max_trade_usd:
        return LimitsCheck(
            ok=False,
            code="max_trade_exceeded",
            reason=f"USD {amount} supera tu límite por operación de USD {limits.max_trade_usd}.",
        )

    projected = _daily_move_total_usd(user) + amount
    if projected > limits.max_daily_move_usd:
        return LimitsCheck(
            ok=False,
            code="daily_cap_exceeded",
            reason=f"Esta operación excede tu tope diario de USD {limits.max_daily_move_usd}.",
        )

    return LimitsCheck(
        ok=True,
        two_step_required=amount > limits.require_2step_above_usd,
    )


def evaluate_move_limits(
    user: User, amount_usd: Decimal | float | int
) -> LimitsCheck:
    amount = _to_decimal(amount_usd)
    limits = _limits_for(user)

    if amount > limits.max_trade_usd:
        return LimitsCheck(
            ok=False,
            code="max_trade_exceeded",
            reason=f"USD {amount} supera tu límite por operación de USD {limits.max_trade_usd}.",
        )

    projected = _daily_move_total_usd(user) + amount
    if projected > limits.max_daily_move_usd:
        return LimitsCheck(
            ok=False,
            code="daily_cap_exceeded",
            reason=f"Esta operación excede tu tope diario de USD {limits.max_daily_move_usd}.",
        )

    return LimitsCheck(
        ok=True,
        two_step_required=amount > limits.require_2step_above_usd,
    )


@transaction.atomic
def create_pending_decision(
    *,
    user: User,
    channel: str,
    user_message: str,
    tool_name: str,
    tool_args: dict[str, Any],
    preview: dict[str, Any],
    agent_reasoning: str = "",
) -> AgentDecision:
    return AgentDecision.objects.create(
        user=user,
        channel=channel,
        user_message=user_message,
        agent_reasoning=agent_reasoning,
        tools_called=[{"tool": tool_name, "args": tool_args, "preview": preview}],
        requires_confirmation=True,
    )


def get_pending_decision(user: User, decision_id: int) -> AgentDecision:
    return AgentDecision.objects.get(
        id=decision_id,
        user=user,
        requires_confirmation=True,
        executed=False,
    )


@transaction.atomic
def mark_executed(decision: AgentDecision, *, wallbit_tx_uuid: str = "") -> None:
    decision.executed = True
    decision.confirmed_at = timezone.now()
    fields = ["executed", "confirmed_at"]
    if wallbit_tx_uuid:
        decision.wallbit_tx_uuid = wallbit_tx_uuid
        fields.append("wallbit_tx_uuid")
    decision.save(update_fields=fields)


@transaction.atomic
def mark_failed(decision: AgentDecision, *, error: str) -> None:
    decision.error = (error or "")[:8000]
    decision.confirmed_at = timezone.now()
    decision.save(update_fields=["error", "confirmed_at"])
