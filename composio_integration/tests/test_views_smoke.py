"""Smoke test for the DRF views that wrap the Composio integration.

Run with:

    python -m composio_integration.tests.test_views_smoke

Uses DRF's APIClient against the real URL conf and JWT auth. The
Composio singleton is replaced with a fake; the Celery ``delay`` calls
are intercepted so nothing reaches a broker.

Cases covered:
- ConnectUrlView 401 without JWT
- ConnectUrlView 200 + redirect_url with JWT + stubbed client
- CallbackView with invalid state -> redirect to dashboard?gmail=invalid_state
- WebhookView 401 on missing signature headers
- WebhookView 200 drop when the connection is disconnected
- WebhookView 202 + handler.handle_webhook called once on the happy path
"""

from __future__ import annotations

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
        external_id=f"test-composio-views-{username}",
        platform="test",
        username=username,
        first_name="Test",
    )


def _jwt_for(user) -> str:
    from rest_framework_simplejwt.tokens import RefreshToken

    refresh = RefreshToken()
    refresh["user_id"] = user.id
    return str(refresh.access_token)


class _FakeClient:
    def __init__(self, *, connect_dto=None, parsed=None, sig_error=False) -> None:
        self._connect = connect_dto
        self._parsed = parsed
        self._sig_error = sig_error
        self.verify_calls = 0

    def create_connect_session(self, *args, **kwargs):
        return self._connect

    def verify_webhook(self, headers, body):
        from composio_integration.exceptions import SignatureError

        self.verify_calls += 1
        if self._sig_error:
            raise SignatureError("bad sig")
        return self._parsed

    def get_connected_account(self, account_id):  # pragma: no cover - unused here
        return None

    def delete_connected_account(self, account_id):  # pragma: no cover
        pass

    def create_trigger(self, *args, **kwargs):  # pragma: no cover
        pass

    def delete_trigger(self, *args, **kwargs):  # pragma: no cover
        pass


@contextmanager
def _patched_client(fake):
    from composio_integration import client as client_module

    original = client_module._instance
    client_module._instance = fake
    try:
        yield
    finally:
        client_module._instance = original


@contextmanager
def _patched_handler(slug: str, fake_handler):
    from composio_integration import registry

    original = registry._registry.get(slug)
    registry._registry[slug] = fake_handler
    try:
        yield
    finally:
        if original is not None:
            registry._registry[slug] = original
        else:
            registry._registry.pop(slug, None)


class _RecordingHandler:
    """Minimal handler that records ``handle_webhook`` invocations."""

    slug = "gmail"
    trigger_specs: list[dict] = []

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def handle_webhook(self, parsed, connection) -> None:
        self.calls.append((parsed, connection))

    def on_connected(self, connection) -> None:  # pragma: no cover
        pass

    def on_disconnected(self, connection) -> None:  # pragma: no cover
        pass


def _run() -> int:
    from django.test import override_settings
    from rest_framework.test import APIClient

    from composio_integration.client import (
        ConnectRequestDTO,
        ParsedPayload,
    )
    from gmailbot.models import ComposioConnection

    failures: list[str] = []

    def _expect(condition: bool, label: str) -> None:
        marker = "OK" if condition else "FAIL"
        print(f"    [{marker}] {label}")
        if not condition:
            failures.append(label)

    print("\n[1] ConnectUrlView without JWT -> 401")
    client = APIClient()
    resp = client.get("/api/integrations/gmail/connect-url/")
    _expect(resp.status_code == 401, f"status_code == 401 (got {resp.status_code})")

    print("\n[2] ConnectUrlView with JWT + stubbed client -> 200")
    with _rolled_back_atomic():
        user = _make_user("views_connect_ok")
        token = _jwt_for(user)
        fake = _FakeClient(
            connect_dto=ConnectRequestDTO(
                connected_account_id="ca_v",
                redirect_url="https://composio.dev/redirect/v",
                status="INITIATED",
            ),
        )
        with override_settings(
            COMPOSIO_AUTH_CONFIGS={"gmail": "ac_test"},
        ), _patched_client(fake):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            resp = client.get("/api/integrations/gmail/connect-url/")
        _expect(resp.status_code == 200, f"status_code == 200 (got {resp.status_code})")
        body = resp.json()
        _expect(
            body.get("redirect_url") == "https://composio.dev/redirect/v",
            "redirect_url field present",
        )
        _expect(
            body.get("connected_account_id") == "ca_v",
            "connected_account_id field present",
        )

    print("\n[3] CallbackView with invalid state -> redirect with flag")
    with override_settings(FRONTEND_URL="https://tresqu.com"):
        client = APIClient()
        resp = client.get(
            "/api/integrations/gmail/callback/",
            {"state": "not-a-jwt", "status": "success", "connected_account_id": "ca_x"},
        )
        # ``redirect()`` from Django returns 302 by default.
        _expect(
            resp.status_code in (301, 302),
            f"redirect status (got {resp.status_code})",
        )
        location = resp.get("Location", "") if hasattr(resp, "get") else ""
        _expect(
            "gmail=invalid_state" in location,
            f"Location contains 'gmail=invalid_state' (got '{location}')",
        )

    print("\n[4] WebhookView without signature headers -> 401")
    client = APIClient()
    resp = client.post(
        "/api/integrations/gmail/composio-webhook/",
        data=b"{}",
        content_type="application/json",
    )
    # The view first runs verify_webhook; without a fake client the
    # singleton tries to use the real SDK. To keep this test hermetic
    # we install a fake that raises SignatureError.
    fake_sig = _FakeClient(sig_error=True)
    with _patched_client(fake_sig):
        client = APIClient()
        resp = client.post(
            "/api/integrations/gmail/composio-webhook/",
            data=b"{}",
            content_type="application/json",
        )
    _expect(
        resp.status_code == 401,
        f"status_code == 401 on bad signature (got {resp.status_code})",
    )

    print("\n[5] WebhookView signature OK + connection disconnected -> 200 drop")
    with _rolled_back_atomic():
        user = _make_user("views_webhook_disc")
        ComposioConnection.objects.create(
            user=user,
            status=ComposioConnection.STATUS_DISCONNECTED,
            connected_account_id="ca_d",
        )
        parsed = ParsedPayload(
            version="V3",
            type="composio.trigger.message",
            metadata={
                "trigger_slug": "GMAIL_NEW_GMAIL_MESSAGE",
                "user_id": str(user.id),
                "connected_account_id": "ca_d",
            },
            data={"id": "msg_disc", "subject": "x", "from": "a@b.c"},
            raw={},
        )
        handler = _RecordingHandler()
        fake = _FakeClient(parsed=parsed)
        with _patched_client(fake), _patched_handler("gmail", handler):
            client = APIClient()
            resp = client.post(
                "/api/integrations/gmail/composio-webhook/",
                data=b"{}",
                content_type="application/json",
            )
        _expect(resp.status_code == 200, f"status_code == 200 drop (got {resp.status_code})")
        _expect(len(handler.calls) == 0, "handler.handle_webhook NOT called")

    print("\n[6] WebhookView signature OK + active connection -> 202 + handler called")
    with _rolled_back_atomic():
        user = _make_user("views_webhook_ok")
        conn = ComposioConnection.objects.create(
            user=user,
            status=ComposioConnection.STATUS_ACTIVE,
            connected_account_id="ca_h",
        )
        parsed = ParsedPayload(
            version="V3",
            type="composio.trigger.message",
            metadata={
                "trigger_slug": "GMAIL_NEW_GMAIL_MESSAGE",
                "user_id": str(user.id),
                "connected_account_id": "ca_h",
            },
            data={"id": "msg_h", "subject": "Test", "from": "noreply@x.com"},
            raw={},
        )
        handler = _RecordingHandler()
        fake = _FakeClient(parsed=parsed)
        with _patched_client(fake), _patched_handler("gmail", handler):
            client = APIClient()
            resp = client.post(
                "/api/integrations/gmail/composio-webhook/",
                data=b"{}",
                content_type="application/json",
            )
        _expect(resp.status_code == 202, f"status_code == 202 (got {resp.status_code})")
        _expect(len(handler.calls) == 1, "handler.handle_webhook called once")
        if handler.calls:
            received_parsed, received_conn = handler.calls[0]
            _expect(
                received_conn.id == conn.id,
                "handler received the correct ComposioConnection",
            )
            _expect(
                received_parsed.data.get("id") == "msg_h",
                "handler received the right ParsedPayload",
            )

    if failures:
        print(f"\nVIEWS SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nVIEWS SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
