"""Smoke test for ``gmailbot.composio_handler.GmailComposioHandler``.

Run with:

    python -m gmailbot.tests.test_composio_handler_smoke

We exercise the webhook fan-out without mocking the DB and without
running Celery: the ``process_gmail_message_async.delay`` call is
intercepted via monkey-patch so we can assert it was scheduled exactly
once on the happy path and zero times on the skip paths.

Cases covered:
- happy path: ProcessedEmail created, ai_response carries the raw email,
  task scheduled once; replaying the same payload is idempotent.
- payload without a gmail_message_id is dropped silently (no row).
- payload for an already-processed email does not enqueue the task again.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager


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
        external_id=f"test-gmail-handler-{username}",
        platform="test",
        username=username,
        first_name="Test",
    )


def _make_connection(user):
    from gmailbot.models import ComposioConnection

    return ComposioConnection.objects.create(
        user=user,
        status=ComposioConnection.STATUS_ACTIVE,
        connected_account_id=f"ca_handler_{user.id}",
        google_email="x@gmail.com",
    )


@contextmanager
def _patched_delay():
    """Capture ``process_gmail_message_async.delay`` invocations."""
    from gmailbot import composio_tasks

    original = composio_tasks.process_gmail_message_async.delay
    calls: list[tuple] = []

    def _capture(*args, **kwargs):
        calls.append((args, kwargs))

    composio_tasks.process_gmail_message_async.delay = _capture  # type: ignore[assignment]
    try:
        yield calls
    finally:
        composio_tasks.process_gmail_message_async.delay = original  # type: ignore[assignment]


def _make_parsed(*, message_id: str, user_id: int):
    from composio_integration.client import ParsedPayload

    return ParsedPayload(
        version="V3",
        type="composio.trigger.message",
        metadata={
            "trigger_slug": "GMAIL_NEW_GMAIL_MESSAGE",
            "user_id": str(user_id),
            "connected_account_id": "ca_handler",
        },
        data={
            "id": message_id,
            "subject": "Tu compra en Amazon",
            "from": "no-reply@amazon.com",
            "message_text": "Gracias por tu compra. Total: $42.00",
            "thread_id": "thr_1",
            "date": "2026-05-26T10:00:00Z",
        },
        raw={},
    )


def _run() -> int:
    from gmailbot.composio_handler import GmailComposioHandler
    from gmailbot.models import ProcessedEmail

    failures: list[str] = []

    def _expect(condition: bool, label: str) -> None:
        marker = "OK" if condition else "FAIL"
        print(f"    [{marker}] {label}")
        if not condition:
            failures.append(label)

    handler = GmailComposioHandler()

    print("\n[1] happy path: row created + task scheduled + idempotent replay")
    with _rolled_back_atomic():
        user = _make_user("handler_ok")
        conn = _make_connection(user)
        parsed = _make_parsed(message_id="msg_001", user_id=user.id)

        with _patched_delay() as calls:
            handler.handle_webhook(parsed, conn)

            rows = ProcessedEmail.objects.filter(
                user=user, composio_connection=conn, gmail_message_id="msg_001"
            )
            _expect(rows.count() == 1, "ProcessedEmail row created")
            row = rows.first()
            _expect(row is not None and row.subject.startswith("Tu compra"), "subject persisted")
            _expect(
                row is not None and row.sender == "no-reply@amazon.com",
                "sender persisted",
            )
            # ai_response carries the raw email JSON for the pipeline.
            try:
                raw_envelope = json.loads(row.ai_response) if row is not None else {}
            except json.JSONDecodeError:
                raw_envelope = {}
            _expect(
                "_raw_email" in raw_envelope,
                "ai_response contains _raw_email envelope",
            )
            _expect(
                raw_envelope.get("_raw_email", {}).get("body", "").startswith("Gracias"),
                "raw body preserved",
            )
            _expect(len(calls) == 1, "process_gmail_message_async.delay called once")

            # Replay: a second webhook with the same gmail_message_id must
            # be a no-op (already pending in this test, so it WILL re-enqueue
            # the pending row but MUST NOT create a duplicate row).
            handler.handle_webhook(parsed, conn)
            rows = ProcessedEmail.objects.filter(
                user=user, composio_connection=conn, gmail_message_id="msg_001"
            )
            _expect(rows.count() == 1, "replay does not create a duplicate row")

    print("\n[2] missing gmail_message_id -> no row, no task")
    with _rolled_back_atomic():
        user = _make_user("handler_no_id")
        conn = _make_connection(user)

        from composio_integration.client import ParsedPayload

        empty_parsed = ParsedPayload(
            version="V3",
            type="composio.trigger.message",
            metadata={"connected_account_id": "ca_handler"},
            data={},  # no id/message_id/gmail_message_id
            raw={},
        )
        with _patched_delay() as calls:
            handler.handle_webhook(empty_parsed, conn)
        _expect(
            ProcessedEmail.objects.filter(user=user).count() == 0,
            "no ProcessedEmail row created",
        )
        _expect(len(calls) == 0, "task not scheduled")

    print("\n[3] already-processed email is not re-enqueued")
    with _rolled_back_atomic():
        user = _make_user("handler_dup")
        conn = _make_connection(user)
        ProcessedEmail.objects.create(
            user=user,
            composio_connection=conn,
            gmail_message_id="msg_dup",
            processing_status="processed",
            subject="old",
            sender="x@y.z",
        )
        parsed = _make_parsed(message_id="msg_dup", user_id=user.id)

        with _patched_delay() as calls:
            handler.handle_webhook(parsed, conn)
        _expect(
            ProcessedEmail.objects.filter(user=user).count() == 1,
            "no extra ProcessedEmail row",
        )
        _expect(len(calls) == 0, "task not re-enqueued for already-processed row")

    if failures:
        print(f"\nGMAIL HANDLER SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nGMAIL HANDLER SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
