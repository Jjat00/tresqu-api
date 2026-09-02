"""Decision lifecycle: one confirmation reaches Wallbit, and only once.

Regression tests for 2026-09-02:
- a decision whose execution FAILED stayed confirmable (``executed=False`` was
  the only filter), so tapping the old button re-placed the order;
- a timeout was reported as "rechazada" and followed by a second POST (LIMIT
  fallback) even though the first order may have filled.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from users.models import User
from wallbit.agent_safety import (
    claim_pending_decision,
    create_pending_decision,
    get_pending_decision,
    mark_cancelled,
    mark_failed,
)
from wallbit.client import (
    RateLimitState,
    WallbitResponse,
    WallbitUncertainError,
    WallbitValidationError,
)
from wallbit.confirmation_actions import (
    UNCERTAIN_REPLY,
    cancel_pending_decision,
    execute_confirmed_decision,
)
from wallbit.executors import execute_decision
from wallbit.models import AgentDecision, WallbitAccount, WallbitTxMirror
from wallbit.tasks import (
    RECONCILE_MAX_ATTEMPTS,
    find_transaction_for_decision,
    reconcile_uncertain_decision,
)


class FakeClient:
    """Stands in for WallbitClient inside the executors."""

    def __init__(self, outcome):
        self.outcome = outcome  # exception instance or response payload
        self.posts: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, path, *, json=None):
        self.posts.append((path, json or {}))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return WallbitResponse(
            status=201, data=self.outcome, rate_limit=RateLimitState(None, None, None)
        )


@contextmanager
def _patched_side_effects(fake: FakeClient):
    with mock.patch("wallbit.executors._client", return_value=fake), mock.patch(
        "wallbit.tasks.sync_wallbit_transactions.delay"
    ) as sync_delay, mock.patch(
        "wallbit.tasks.reconcile_uncertain_decision.apply_async"
    ) as reconcile:
        yield sync_delay, reconcile


def _trade_args(**overrides):
    args = {"action": "BUY", "symbol": "SPCX", "amount_usd": "20.0", "order_type": "MARKET"}
    args.update(overrides)
    return args


class DecisionLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            external_id="wa_573000000001", platform="whatsapp", username="t", first_name="T"
        )
        self.account = WallbitAccount.objects.create(
            user=self.user, encrypted_api_key="enc", status=WallbitAccount.CONNECTED
        )

    def _decision(self, **overrides) -> AgentDecision:
        return create_pending_decision(
            user=self.user,
            channel="whatsapp",
            user_message="Compra 20 usd en Spacex",
            tool_name="wallbit_place_trade",
            tool_args=_trade_args(**overrides),
            preview={"summary": "BUY SPCX por USD 20.0"},
        )

    # -- claim ---------------------------------------------------------------

    def test_claim_is_exclusive(self):
        decision = self._decision()
        claimed = claim_pending_decision(self.user, decision.id)
        self.assertEqual(claimed.status, AgentDecision.EXECUTING)
        self.assertIsNotNone(claimed.confirmed_at)
        with self.assertRaises(AgentDecision.DoesNotExist):
            claim_pending_decision(self.user, decision.id)

    def test_failed_decision_is_not_confirmable(self):
        decision = self._decision()
        mark_failed(decision, error="The shares field is required when order type is LIMIT.")
        with self.assertRaises(AgentDecision.DoesNotExist):
            get_pending_decision(self.user, decision.id)
        with self.assertRaises(AgentDecision.DoesNotExist):
            claim_pending_decision(self.user, decision.id)

    def test_cancelled_decision_is_not_confirmable(self):
        decision = self._decision()
        mark_cancelled(decision)
        self.assertEqual(decision.status, AgentDecision.CANCELLED)
        with self.assertRaises(AgentDecision.DoesNotExist):
            claim_pending_decision(self.user, decision.id)

    def test_cancel_via_chat_marks_cancelled(self):
        decision = self._decision()
        reply = cancel_pending_decision(self.user, decision.id)
        decision.refresh_from_db()
        self.assertIn("cancelada", reply)
        self.assertEqual(decision.status, AgentDecision.CANCELLED)

    # -- execution -----------------------------------------------------------

    def test_timeout_freezes_decision_without_second_post(self):
        decision = self._decision()
        claim_pending_decision(self.user, decision.id)
        fake = FakeClient(WallbitUncertainError("POST /trades: Wallbit no respondió (ReadTimeout)"))
        with _patched_side_effects(fake) as (sync_delay, reconcile):
            result = execute_decision(decision, self.account)

        decision.refresh_from_db()
        self.assertEqual(len(fake.posts), 1, "no LIMIT fallback after a timeout")
        self.assertTrue(result.get("uncertain"))
        self.assertFalse(result.get("ok"))
        self.assertEqual(decision.status, AgentDecision.UNCERTAIN)
        self.assertFalse(decision.executed)
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.kwargs["args"], (decision.id,))
        sync_delay.assert_called_once_with(self.account.id)
        with self.assertRaises(AgentDecision.DoesNotExist):
            claim_pending_decision(self.user, decision.id)

    def test_rejection_is_definitive_and_surfaces_wallbit_message(self):
        decision = self._decision()
        claim_pending_decision(self.user, decision.id)
        fake = FakeClient(
            WallbitValidationError(
                "The amount is insufficient to cover the transaction fee.", status=422
            )
        )
        with _patched_side_effects(fake) as (sync_delay, reconcile):
            result = execute_decision(decision, self.account)

        decision.refresh_from_db()
        self.assertEqual(len(fake.posts), 1)
        self.assertEqual(decision.status, AgentDecision.FAILED)
        self.assertIn("insufficient", result["error"])
        reconcile.assert_not_called()
        sync_delay.assert_called_once()

    def test_success_marks_executed_and_links_tx(self):
        decision = self._decision()
        claim_pending_decision(self.user, decision.id)
        fake = FakeClient({"data": {"uuid": "tx-123"}})
        with _patched_side_effects(fake):
            result = execute_decision(decision, self.account)

        decision.refresh_from_db()
        self.assertTrue(result["ok"])
        self.assertEqual(decision.status, AgentDecision.EXECUTED)
        self.assertTrue(decision.executed)
        self.assertEqual(decision.wallbit_tx_uuid, "tx-123")
        path, body = fake.posts[0]
        self.assertEqual(path, "/trades")
        self.assertEqual(body["order_type"], "MARKET")
        self.assertEqual(body["amount"], 20.0)
        self.assertNotIn("shares", body)

    def test_limit_order_is_sized_in_shares(self):
        decision = self._decision(
            order_type="LIMIT", limit_price="143.52", time_in_force="DAY"
        )
        claim_pending_decision(self.user, decision.id)
        fake = FakeClient({"data": {"uuid": "tx-limit"}})
        with _patched_side_effects(fake):
            execute_decision(decision, self.account)

        _, body = fake.posts[0]
        self.assertEqual(body["order_type"], "LIMIT")
        self.assertEqual(body["limit_price"], 143.52)
        self.assertEqual(body["time_in_force"], "DAY")
        self.assertNotIn("amount", body)
        # 20 / 143.52 = 0.13935… → rounded DOWN to 4 dp; never above the amount.
        self.assertEqual(body["shares"], 0.1393)
        self.assertLessEqual(Decimal(str(body["shares"])) * Decimal("143.52"), Decimal("20.0"))

    # -- chat wrapper --------------------------------------------------------

    def test_chat_confirmation_reports_uncertain_not_rejected(self):
        decision = self._decision()
        fake = FakeClient(WallbitUncertainError("timeout"))
        with _patched_side_effects(fake):
            reply = execute_confirmed_decision(self.user, decision.id)
            second = execute_confirmed_decision(self.user, decision.id)

        self.assertEqual(reply, UNCERTAIN_REPLY)
        self.assertNotIn("rechazó", reply)
        self.assertIn("ya fue resuelta", second)
        self.assertEqual(len(fake.posts), 1, "the second tap must not reach Wallbit")


class ReconciliationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            external_id="wa_573000000002", platform="whatsapp", username="r", first_name="R"
        )
        self.account = WallbitAccount.objects.create(
            user=self.user, encrypted_api_key="enc", status=WallbitAccount.CONNECTED
        )
        self.decision = create_pending_decision(
            user=self.user,
            channel="whatsapp",
            user_message="Compra 20 usd en Spacex",
            tool_name="wallbit_place_trade",
            tool_args=_trade_args(),
            preview={"summary": "BUY SPCX por USD 20.0"},
        )
        claim_pending_decision(self.user, self.decision.id)
        self.decision.refresh_from_db()
        from wallbit.agent_safety import mark_uncertain

        mark_uncertain(self.decision, error="timeout")

    def _mirror(self, uuid: str, symbol="SPCX", amount="20.00", minutes_ago=0, direction="BUY"):
        return WallbitTxMirror.objects.create(
            account=self.account,
            wallbit_uuid=uuid,
            tx_type="TRADE",
            status="COMPLETED",
            source_currency="USD",
            dest_currency=symbol,
            source_amount=Decimal(amount),
            dest_amount=Decimal("0.14"),
            created_at_wallbit=timezone.now() - timezone.timedelta(minutes=minutes_ago),
            raw={"trade_info": {"symbol": symbol, "direction": direction}},
        )

    def test_matches_fresh_trade_with_same_symbol_and_amount(self):
        self._mirror("old", minutes_ago=60)  # too old
        self._mirror("other", symbol="GOOG")  # other symbol
        hit = self._mirror("fresh")
        self.assertEqual(find_transaction_for_decision(self.decision, self.account), hit)

    def test_skips_transaction_claimed_by_another_decision(self):
        taken = self._mirror("taken")
        other = create_pending_decision(
            user=self.user, channel="web", user_message="x",
            tool_name="wallbit_place_trade", tool_args=_trade_args(), preview={},
        )
        other.wallbit_tx_uuid = taken.wallbit_uuid
        other.save(update_fields=["wallbit_tx_uuid"])
        self.assertIsNone(find_transaction_for_decision(self.decision, self.account))

    def test_task_settles_to_executed_and_notifies(self):
        hit = self._mirror("fresh")
        with mock.patch("wallbit.tasks.run_wallbit_sync", return_value={"ok": True}), mock.patch(
            "wallbit.tasks.notify_decision_user"
        ) as notify:
            result = reconcile_uncertain_decision.apply(args=(self.decision.id,))

        self.decision.refresh_from_db()
        self.assertEqual(result.state, "SUCCESS")
        self.assertEqual(self.decision.status, AgentDecision.EXECUTED)
        self.assertTrue(self.decision.executed)
        self.assertEqual(self.decision.wallbit_tx_uuid, hit.wallbit_uuid)
        notify.assert_called_once()
        self.assertIn("SÍ se ejecutó", notify.call_args.args[1])

    def test_task_retries_before_giving_up(self):
        # In eager mode Celery runs the retries inline, so one apply() exercises
        # the whole ladder: every attempt re-syncs, only the last one settles.
        with mock.patch("wallbit.tasks.run_wallbit_sync", return_value={"ok": True}) as sync, mock.patch(
            "wallbit.tasks.notify_decision_user"
        ) as notify:
            result = reconcile_uncertain_decision.apply(args=(self.decision.id,))

        self.decision.refresh_from_db()
        self.assertEqual(result.state, "SUCCESS")
        self.assertEqual(sync.call_count, RECONCILE_MAX_ATTEMPTS)
        self.assertEqual(self.decision.status, AgentDecision.FAILED)
        notify.assert_called_once()
        self.assertIn("NO se ejecutó", notify.call_args.args[1])

    def test_task_gives_up_as_failed_after_last_attempt(self):
        with mock.patch("wallbit.tasks.run_wallbit_sync", return_value={"ok": True}), mock.patch(
            "wallbit.tasks.notify_decision_user"
        ) as notify:
            result = reconcile_uncertain_decision.apply(
                args=(self.decision.id,), retries=RECONCILE_MAX_ATTEMPTS - 1
            )

        self.decision.refresh_from_db()
        self.assertEqual(result.state, "SUCCESS")
        self.assertEqual(self.decision.status, AgentDecision.FAILED)
        self.assertFalse(self.decision.executed)
        self.assertIn("NO se ejecutó", notify.call_args.args[1])

    def test_task_ignores_decisions_that_are_not_uncertain(self):
        self.decision.status = AgentDecision.EXECUTED
        self.decision.save(update_fields=["status"])
        with mock.patch("wallbit.tasks.run_wallbit_sync") as sync:
            result = reconcile_uncertain_decision.apply(args=(self.decision.id,))
        self.assertTrue(result.result.get("skipped"))
        sync.assert_not_called()
