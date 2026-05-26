"""Smoke test for ``ToolkitConnectionService``.

Run with:

    python -m composio_integration.tests.test_services_smoke

Real DB writes inside ``transaction.atomic`` blocks that are always
rolled back. The Composio SDK is swapped out via a fake injected into
``composio_integration.client._instance`` (singleton). No outbound HTTP.

Cases covered:
- start_connect_flow happy path: pending row created, redirect_url returned
- start_connect_flow with empty COMPOSIO_AUTH_CONFIGS raises permanent
- complete_connect_flow happy path: status -> active, google_email set
- complete_connect_flow user_id mismatch raises permanent
- complete_connect_flow nonce replay raises StateTokenError
- complete_connect_flow status='failed' marks row failed and raises
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
        external_id=f"test-composio-svc-{username}",
        platform="test",
        username=username,
        first_name="Test",
    )


class _FakeClient:
    """Stand-in for ``ComposioClient`` swapped into the singleton slot."""

    def __init__(
        self,
        *,
        connect_dto=None,
        account_dto=None,
        on_create=None,
    ) -> None:
        self._connect_dto = connect_dto
        self._account_dto = account_dto
        self._on_create = on_create
        self.create_calls: list[tuple] = []
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    def create_connect_session(
        self,
        user_id: str,
        toolkit: str,
        auth_config_id: str,
        callback_url: str,
    ):
        self.create_calls.append((user_id, toolkit, auth_config_id, callback_url))
        if self._on_create is not None:
            return self._on_create(user_id, toolkit, auth_config_id, callback_url)
        return self._connect_dto

    def get_connected_account(self, account_id: str):
        self.get_calls.append(account_id)
        return self._account_dto

    def delete_connected_account(self, account_id: str) -> None:
        self.delete_calls.append(account_id)

    def create_trigger(self, *args, **kwargs):  # pragma: no cover - unused here
        from composio_integration.client import TriggerDTO

        return TriggerDTO(trigger_id="ti_fake", slug="x", status="active")

    def delete_trigger(self, trigger_id: str) -> None:  # pragma: no cover
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
def _patched_provision():
    """Stub ``provision_triggers_async.delay`` so we never enqueue Celery."""
    from composio_integration import tasks

    original = tasks.provision_triggers_async.delay
    calls: list[tuple] = []

    def _capture(*args, **kwargs):
        calls.append((args, kwargs))

    tasks.provision_triggers_async.delay = _capture  # type: ignore[assignment]
    try:
        yield calls
    finally:
        tasks.provision_triggers_async.delay = original  # type: ignore[assignment]


def _run() -> int:
    from django.test import RequestFactory, override_settings

    from composio_integration.client import (
        ConnectedAccountDTO,
        ConnectRequestDTO,
    )
    from composio_integration.exceptions import (
        ComposioPermanentError,
        StateTokenError,
    )
    from composio_integration.services import ToolkitConnectionService
    from composio_integration.state_token import encode_state
    from gmailbot.models import ComposioConnection

    # Stub request used to derive the callback URL — el resolver pide
    # un request real (build_absolute_uri) en lugar de leer un settings.
    _request_factory = RequestFactory()

    def _stub_request():
        return _request_factory.get("/", HTTP_HOST="api.tresqu.test")

    failures: list[str] = []

    def _expect(condition: bool, label: str) -> None:
        marker = "OK" if condition else "FAIL"
        print(f"    [{marker}] {label}")
        if not condition:
            failures.append(label)

    print("\n[1] start_connect_flow happy path")
    with _rolled_back_atomic():
        user = _make_user("svc_start_ok")
        fake = _FakeClient(
            connect_dto=ConnectRequestDTO(
                connected_account_id="ca_ok",
                redirect_url="https://composio.dev/redirect/x",
                status="INITIATED",
            ),
        )
        with override_settings(
            COMPOSIO_AUTH_CONFIGS={"gmail": "ac_test"},
        ), _patched_client(fake):
            dto = ToolkitConnectionService().start_connect_flow(
                user, "gmail", request=_stub_request()
            )
        _expect(
            dto.redirect_url == "https://composio.dev/redirect/x",
            "redirect_url returned",
        )
        _expect(dto.connected_account_id == "ca_ok", "connected_account_id returned")
        row = ComposioConnection.objects.filter(user=user).first()
        _expect(row is not None, "ComposioConnection row created")
        _expect(
            row is not None and row.status == ComposioConnection.STATUS_PENDING,
            "status=pending",
        )
        _expect(
            row is not None and row.connected_account_id == "ca_ok",
            "connected_account_id persisted",
        )
        _expect(len(fake.create_calls) == 1, "SDK called exactly once")

    print("\n[2] start_connect_flow without auth_config -> permanent")
    with _rolled_back_atomic():
        user = _make_user("svc_no_auth_config")
        fake = _FakeClient()
        with override_settings(
            COMPOSIO_AUTH_CONFIGS={"gmail": ""},
        ), _patched_client(fake):
            raised = False
            try:
                ToolkitConnectionService().start_connect_flow(
                    user, "gmail", request=_stub_request()
                )
            except ComposioPermanentError as exc:
                raised = "auth_config_id" in str(exc)
        _expect(raised, "ComposioPermanentError raised for empty config")
        _expect(len(fake.create_calls) == 0, "SDK never called")

    print("\n[3] complete_connect_flow happy path")
    with _rolled_back_atomic():
        user = _make_user("svc_complete_ok")
        ComposioConnection.objects.create(
            user=user,
            status=ComposioConnection.STATUS_PENDING,
            connected_account_id="ca_x",
        )
        fake = _FakeClient(
            account_dto=ConnectedAccountDTO(
                id="ca_x",
                user_id=str(user.id),
                toolkit="gmail",
                status="ACTIVE",
                auth_config_id="ac_test",
                profile_email="x@gmail.com",
            ),
        )
        state = encode_state(user.id, "gmail")
        with override_settings(
            COMPOSIO_AUTH_CONFIGS={"gmail": "ac_test"},
        ), _patched_client(fake), _patched_provision() as enqueued:
            conn = ToolkitConnectionService().complete_connect_flow(
                state, "success", "ca_x"
            )
        _expect(
            conn.status == ComposioConnection.STATUS_ACTIVE,
            "status flipped to active",
        )
        _expect(conn.google_email == "x@gmail.com", "google_email persisted")
        _expect(len(enqueued) == 1, "provision_triggers_async.delay called once")

    print("\n[4] complete_connect_flow user_id mismatch -> permanent")
    with _rolled_back_atomic():
        user = _make_user("svc_complete_mismatch")
        ComposioConnection.objects.create(
            user=user,
            status=ComposioConnection.STATUS_PENDING,
            connected_account_id="ca_y",
        )
        fake = _FakeClient(
            account_dto=ConnectedAccountDTO(
                id="ca_y",
                user_id="999",  # not user.id
                toolkit="gmail",
                status="ACTIVE",
                profile_email="evil@gmail.com",
            ),
        )
        state = encode_state(user.id, "gmail")
        raised = False
        with override_settings(
            COMPOSIO_AUTH_CONFIGS={"gmail": "ac_test"},
        ), _patched_client(fake), _patched_provision() as enqueued:
            try:
                ToolkitConnectionService().complete_connect_flow(
                    state, "success", "ca_y"
                )
            except ComposioPermanentError as exc:
                raised = "ownership" in str(exc).lower()
        _expect(raised, "ComposioPermanentError raised on ownership mismatch")
        # Row should still be pending (no transition)
        row = ComposioConnection.objects.filter(user=user).first()
        _expect(
            row is not None and row.status == ComposioConnection.STATUS_PENDING,
            "connection stayed pending",
        )
        _expect(len(enqueued) == 0, "provision NOT enqueued")

    print("\n[5] complete_connect_flow nonce replay")
    with _rolled_back_atomic():
        user = _make_user("svc_complete_replay")
        ComposioConnection.objects.create(
            user=user,
            status=ComposioConnection.STATUS_PENDING,
            connected_account_id="ca_z",
        )
        fake = _FakeClient(
            account_dto=ConnectedAccountDTO(
                id="ca_z",
                user_id=str(user.id),
                toolkit="gmail",
                status="ACTIVE",
                profile_email="z@gmail.com",
            ),
        )
        state = encode_state(user.id, "gmail")
        with override_settings(
            COMPOSIO_AUTH_CONFIGS={"gmail": "ac_test"},
        ), _patched_client(fake), _patched_provision():
            ToolkitConnectionService().complete_connect_flow(state, "success", "ca_z")
            raised = False
            try:
                ToolkitConnectionService().complete_connect_flow(
                    state, "success", "ca_z"
                )
            except StateTokenError as exc:
                raised = "nonce" in str(exc).lower()
        _expect(raised, "second use raises StateTokenError('nonce ...')")

    print("\n[6] complete_connect_flow status=failed -> row.failed + permanent")
    with _rolled_back_atomic():
        user = _make_user("svc_complete_failed")
        ComposioConnection.objects.create(
            user=user,
            status=ComposioConnection.STATUS_PENDING,
            connected_account_id="ca_f",
        )
        fake = _FakeClient()  # SDK should not be called on failed status
        state = encode_state(user.id, "gmail")
        raised = False
        with override_settings(
            COMPOSIO_AUTH_CONFIGS={"gmail": "ac_test"},
        ), _patched_client(fake), _patched_provision():
            try:
                ToolkitConnectionService().complete_connect_flow(state, "failed", "ca_f")
            except ComposioPermanentError as exc:
                raised = "failed" in str(exc).lower()
        _expect(raised, "ComposioPermanentError raised on failed status")
        row = ComposioConnection.objects.filter(user=user).first()
        _expect(
            row is not None and row.status == ComposioConnection.STATUS_FAILED,
            "row marked failed",
        )
        _expect(
            row is not None and "status=failed" in row.last_error,
            "last_error populated",
        )
        _expect(len(fake.get_calls) == 0, "SDK get_connected_account not called")

    if failures:
        print(f"\nSERVICES SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSERVICES SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
