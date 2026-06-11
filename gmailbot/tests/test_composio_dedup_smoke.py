"""Smoke test for the cross-email duplicate detection in
``gmailbot.composio_pipeline``.

Run with:

    python -m gmailbot.tests.test_composio_dedup_smoke

The same purchase often arrives as TWO different emails (merchant receipt +
card issuer notification) within minutes, with the same amount. The pipeline
must create ONE transaction and mark the second email as ``duplicate``.

External calls are monkey-patched (AI parser, LLM judge, embeddings,
WhatsApp); the DB is real but every case rolls back.

Cases covered:
- same transaction_reference  -> duplicate, no second Expense, no judge call
- different references        -> NOT duplicate, judge not called, 2 Expenses
- judge says duplicate        -> duplicate + merchant of existing improved
- judge says not duplicate    -> 2 Expenses
- candidate outside window    -> no dedup attempt, 2 Expenses
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import timedelta


class _Rollback(Exception):
    """Sentinel to force ``transaction.atomic`` to roll back."""


@contextmanager
def _rolled_back_atomic():
    from django.db import transaction

    try:
        with transaction.atomic():
            yield
            raise _Rollback
    except _Rollback:
        pass


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _make_user(username: str):
    from users.models import User

    return User.objects.create(
        external_id=f"test-gmail-dedup-{username}",
        platform="test",
        username=username,
        first_name="Test",
        default_currency="USD",
    )


def _make_connection(user):
    from gmailbot.models import ComposioConnection

    return ComposioConnection.objects.create(
        user=user,
        status=ComposioConnection.STATUS_ACTIVE,
        connected_account_id=f"ca_dedup_{user.id}",
        google_email="x@gmail.com",
    )


def _make_pending_email(
    user, conn, *, message_id: str, subject: str, sender: str, body: str
):
    from gmailbot.models import ProcessedEmail

    pe = ProcessedEmail.objects.create(
        user=user,
        composio_connection=conn,
        gmail_message_id=message_id,
        subject=subject,
        sender=sender,
        processing_status="pending",
    )
    pe.ai_response = json.dumps(
        {
            "_raw_email": {
                "subject": subject,
                "sender": sender,
                "body": body,
                "thread_id": "thr_1",
                "date_raw": "",
            }
        },
        ensure_ascii=False,
    )
    pe.save(update_fields=["ai_response"])
    return pe


def _ai_result(**overrides) -> dict:
    base = {
        "is_purchase": True,
        "transaction_type": "expense",
        "payment_status": "success",
        "amount": 48.12,
        "currency": "USD",
        "merchant": "Compra Smart Card",
        "date": "2026-06-10",
        "confidence": 0.95,
        "suggested_category": None,
        "category_confidence": 0.0,
        "transaction_reference": None,
    }
    base.update(overrides)
    return base


class _Patcher:
    """Minimal monkey-patch helper with restore."""

    def __init__(self):
        self._saved: list[tuple] = []

    def patch(self, obj, attr, value):
        self._saved.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def restore(self):
        for obj, attr, original in reversed(self._saved):
            setattr(obj, attr, original)
        self._saved.clear()


@contextmanager
def _patched_pipeline(*, parse_results: list[dict], judge_result):
    """Patch parser, judge, embeddings and WhatsApp on the pipeline module.

    ``parse_results`` is consumed in order, one per processed email.
    ``judge_result`` is what ``_judge_duplicate_emails`` returns; pass the
    sentinel string "FAIL_IF_CALLED" to assert the judge is never reached.
    """
    from gmailbot import composio_pipeline, email_processor

    patcher = _Patcher()
    parse_calls: list = list(parse_results)
    judge_calls: list[tuple] = []

    def _fake_parse(*args, **kwargs):
        return parse_calls.pop(0)

    def _fake_judge(new_email, old_email):
        judge_calls.append((new_email, old_email))
        if judge_result == "FAIL_IF_CALLED":
            raise AssertionError("judge LLM should not have been called")
        return judge_result

    class _FakeEmbeddings:
        def embed_query(self, text):
            return [0.0] * 1536

    patcher.patch(composio_pipeline, "parse_purchase_email", _fake_parse)
    patcher.patch(composio_pipeline, "_judge_duplicate_emails", _fake_judge)
    patcher.patch(email_processor, "embeddings", _FakeEmbeddings())
    patcher.patch(
        composio_pipeline,
        "send_transaction_confirmation_whatsapp",
        lambda *a, **kw: "wamid.test",
    )
    try:
        yield judge_calls
    finally:
        patcher.restore()


def _run() -> int:
    from gmailbot.composio_pipeline import process_composio_email
    from gmailbot.models import ProcessedEmail

    failures: list[str] = []

    def _expect(condition: bool, label: str) -> None:
        marker = "OK" if condition else "FAIL"
        print(f"    [{marker}] {label}")
        if not condition:
            failures.append(label)

    def _expenses(user):
        from expenses.models import Expense

        return Expense.objects.filter(user=user)

    def _process_pair(user, conn, first_ai, second_ai, judge_result):
        """Process two emails of the same purchase pattern; return both PEs."""
        pe1 = _make_pending_email(
            user,
            conn,
            message_id="msg_receipt",
            subject="Your receipt from Railway Corporation #2749-9904",
            sender="receipts@railway.com",
            body="Receipt #2749-9904. Total: $48.12",
        )
        pe2 = _make_pending_email(
            user,
            conn,
            message_id="msg_card",
            subject="Compra exitosa con tu Smart Card",
            sender="alertas@wallbit.io",
            body="Compra aprobada por USD 48.12 en RAILWAY CORP",
        )
        with _patched_pipeline(
            parse_results=[first_ai, second_ai], judge_result=judge_result
        ) as judge_calls:
            process_composio_email(pe1)
            process_composio_email(pe2)
        pe1.refresh_from_db()
        pe2.refresh_from_db()
        return pe1, pe2, judge_calls

    print("\n[1] same transaction_reference -> duplicate without judge")
    with _rolled_back_atomic():
        user = _make_user("ref_match")
        conn = _make_connection(user)
        pe1, pe2, _ = _process_pair(
            user,
            conn,
            _ai_result(
                merchant="Railway Corporation",
                transaction_reference="#2749-9904",
            ),
            _ai_result(transaction_reference="2749 9904"),
            judge_result="FAIL_IF_CALLED",
        )
        _expect(pe1.processing_status == "processed", "first email processed")
        _expect(pe2.processing_status == "duplicate", "second email marked duplicate")
        _expect(_expenses(user).count() == 1, "only one Expense created")
        dup_info = json.loads(pe2.ai_response).get("duplicate_of", {})
        _expect(
            dup_info.get("expense_id") == pe1.expense_id,
            "duplicate_of points to first expense",
        )
        _expect(dup_info.get("via") == "reference", "matched via reference")
        _expect(pe2.expense_id is None, "duplicate email has no expense FK")

    print("\n[2] different explicit references -> two real Expenses, no judge")
    with _rolled_back_atomic():
        user = _make_user("ref_differ")
        conn = _make_connection(user)
        pe1, pe2, _ = _process_pair(
            user,
            conn,
            _ai_result(
                merchant="Railway Corporation",
                transaction_reference="#2749-9904",
            ),
            _ai_result(
                merchant="Otra Compra",
                transaction_reference="#0000-1111",
            ),
            judge_result="FAIL_IF_CALLED",
        )
        _expect(pe2.processing_status == "processed", "second email processed")
        _expect(_expenses(user).count() == 2, "two Expenses created")

    print("\n[3] judge says duplicate -> duplicate + merchant improved")
    with _rolled_back_atomic():
        user = _make_user("judge_dup")
        conn = _make_connection(user)
        pe1, pe2, judge_calls = _process_pair(
            user,
            conn,
            _ai_result(merchant="Compra Smart Card"),
            _ai_result(merchant="Railway Corp"),
            judge_result={
                "is_duplicate": True,
                "confidence": 0.95,
                "best_merchant": "Railway Corporation",
            },
        )
        _expect(len(judge_calls) == 1, "judge called exactly once")
        _expect(pe2.processing_status == "duplicate", "second email marked duplicate")
        _expect(_expenses(user).count() == 1, "only one Expense created")
        expense = _expenses(user).first()
        _expect(
            expense.description == "Railway Corporation",
            "existing expense merchant improved by judge",
        )
        dup_info = json.loads(pe2.ai_response).get("duplicate_of", {})
        _expect(dup_info.get("via") == "llm", "matched via llm judge")

    print("\n[4] judge says NOT duplicate -> two real Expenses")
    with _rolled_back_atomic():
        user = _make_user("judge_no")
        conn = _make_connection(user)
        pe1, pe2, judge_calls = _process_pair(
            user,
            conn,
            _ai_result(merchant="Cafe A"),
            _ai_result(merchant="Cafe B"),
            judge_result={
                "is_duplicate": False,
                "confidence": 0.9,
                "best_merchant": None,
            },
        )
        _expect(len(judge_calls) == 1, "judge called exactly once")
        _expect(pe2.processing_status == "processed", "second email processed")
        _expect(_expenses(user).count() == 2, "two Expenses created")

    print("\n[5] candidate outside the minutes window -> no dedup attempt")
    with _rolled_back_atomic():
        from gmailbot.composio_pipeline import DUPLICATE_WINDOW_MINUTES

        user = _make_user("window")
        conn = _make_connection(user)
        pe1 = _make_pending_email(
            user,
            conn,
            message_id="msg_old",
            subject="Compra exitosa con tu Smart Card",
            sender="alertas@wallbit.io",
            body="Compra aprobada por USD 48.12",
        )
        with _patched_pipeline(
            parse_results=[_ai_result(merchant="Compra Smart Card")],
            judge_result="FAIL_IF_CALLED",
        ):
            process_composio_email(pe1)
        pe1.refresh_from_db()
        # Backdate the first email beyond the window, then process a second
        # identical-amount email: it must NOT be considered a duplicate.
        ProcessedEmail.objects.filter(pk=pe1.pk).update(
            created_at=pe1.created_at
            - timedelta(minutes=DUPLICATE_WINDOW_MINUTES + 45)
        )
        pe2 = _make_pending_email(
            user,
            conn,
            message_id="msg_late",
            subject="Compra exitosa con tu Smart Card",
            sender="alertas@wallbit.io",
            body="Compra aprobada por USD 48.12",
        )
        with _patched_pipeline(
            parse_results=[_ai_result(merchant="Compra Smart Card")],
            judge_result="FAIL_IF_CALLED",
        ):
            process_composio_email(pe2)
        pe2.refresh_from_db()
        _expect(pe1.processing_status == "processed", "first email processed")
        _expect(pe2.processing_status == "processed", "late email also processed")
        _expect(_expenses(user).count() == 2, "two Expenses created")

    if failures:
        print(f"\nGMAIL DEDUP SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nGMAIL DEDUP SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
