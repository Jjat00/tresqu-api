"""Smoke test for ``ComposioClient.verify_webhook``.

Run with:

    python -m composio_integration.tests.test_webhook_verify

We exercise the wrapper logic — header lookup, missing-header rejection,
SDK exception translation, V3 envelope normalisation — without ever
hitting the real Composio backend. The strategy is to construct a
ComposioClient instance, then monkey-patch its ``_sdk.triggers`` so the
SDK contract is honoured but no network IO happens.

If the ``composio`` SDK is not installed in this venv the client cannot
be instantiated; the script then prints a clear SKIP and exits 0.
"""

from __future__ import annotations

import os
import sys


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


class _StubVerifyResult:
    """Canned response shaped like the SDK's VerifyWebhookResult."""

    def __init__(
        self,
        version: str = "V3",
        payload: dict | None = None,
        raw: dict | None = None,
    ) -> None:
        self.version = version
        self.payload = payload or {}
        self.raw_payload = raw or {}


class _StubTriggers:
    """Stub for ``client._sdk.triggers`` exposing only ``verify_webhook``."""

    def __init__(
        self,
        *,
        result: _StubVerifyResult | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._result = result
        self._raise = raise_exc
        self.last_call: dict | None = None

    def verify_webhook(self, **kwargs: object) -> _StubVerifyResult:
        self.last_call = dict(kwargs)
        if self._raise is not None:
            raise self._raise
        assert self._result is not None
        return self._result


def _build_client_with_stub(stub: _StubTriggers):
    """Construct ``ComposioClient`` and graft ``stub`` over ``_sdk.triggers``.

    The constructor instantiates the real SDK, so a missing dependency
    causes a clean ``ImportError`` we surface as a SKIP.
    """
    from composio_integration.client import ComposioClient, reset_client_for_tests

    reset_client_for_tests()
    client = ComposioClient(api_key="test-key-not-used")

    class _SdkShim:
        pass

    shim = _SdkShim()
    shim.triggers = stub  # type: ignore[attr-defined]
    client._sdk = shim  # type: ignore[attr-defined]
    return client


def _run() -> int:
    try:
        import composio  # noqa: F401
    except ImportError:
        print(
            "SKIP: composio SDK not installed in this venv — "
            "verify_webhook wrapper cannot be exercised."
        )
        return 0

    from django.test import override_settings

    from composio_integration.exceptions import SignatureError

    failures: list[str] = []

    def _expect(condition: bool, label: str) -> None:
        marker = "OK" if condition else "FAIL"
        print(f"    [{marker}] {label}")
        if not condition:
            failures.append(label)

    print("\n[1] case-insensitive header lookup (Webhook-Id, etc.)")
    sample_payload = {
        "trigger_slug": "GMAIL_NEW_GMAIL_MESSAGE",
        "id": "ti_abc",
        "user_id": "7",
        "toolkit_slug": "gmail",
        "metadata": {
            "connected_account": {
                "id": "ca_abc",
                "auth_config_id": "ac_abc",
            },
        },
        "payload": {"subject": "hello", "id": "msg_1"},
    }
    stub = _StubTriggers(
        result=_StubVerifyResult(
            version="V3",
            payload=sample_payload,
            raw={"type": "composio.trigger.message"},
        )
    )
    with override_settings(COMPOSIO_WEBHOOK_SECRET="whsec_test"):
        client = _build_client_with_stub(stub)
        parsed = client.verify_webhook(
            headers={
                "Webhook-Id": "wh_1",
                "Webhook-Signature": "v1,deadbeef",
                "Webhook-Timestamp": "1234567890",
            },
            body=b'{"x":1}',
        )
    _expect(
        stub.last_call is not None and stub.last_call.get("id") == "wh_1",
        "stub received webhook-id from capitalised header",
    )
    _expect(parsed.version == "V3", "ParsedPayload.version == 'V3'")
    _expect(
        parsed.metadata.get("trigger_slug") == "GMAIL_NEW_GMAIL_MESSAGE",
        "metadata.trigger_slug populated",
    )
    _expect(parsed.metadata.get("user_id") == "7", "metadata.user_id populated")
    _expect(
        parsed.metadata.get("connected_account_id") == "ca_abc",
        "metadata.connected_account_id populated",
    )
    _expect(
        parsed.metadata.get("auth_config_id") == "ac_abc",
        "metadata.auth_config_id populated",
    )
    _expect(parsed.data.get("subject") == "hello", "data.subject preserved")

    print("\n[2] missing webhook-id raises SignatureError (no SDK call)")
    stub_unused = _StubTriggers(result=_StubVerifyResult())
    with override_settings(COMPOSIO_WEBHOOK_SECRET="whsec_test"):
        client = _build_client_with_stub(stub_unused)
        raised = False
        try:
            client.verify_webhook(
                headers={
                    "webhook-signature": "v1,xx",
                    "webhook-timestamp": "1",
                },
                body=b"{}",
            )
        except SignatureError:
            raised = True
    _expect(raised, "SignatureError raised when webhook-id is missing")
    _expect(stub_unused.last_call is None, "SDK was not called")

    print("\n[3] SDK signature failure translates to SignatureError")

    class _FakeSdkSigError(Exception):
        """Mimics composio.exceptions.WebhookSignatureVerificationError."""

    stub_err = _StubTriggers(raise_exc=_FakeSdkSigError("bad signature"))
    with override_settings(COMPOSIO_WEBHOOK_SECRET="whsec_test"):
        client = _build_client_with_stub(stub_err)
        raised = False
        try:
            client.verify_webhook(
                headers={
                    "webhook-id": "wh_1",
                    "webhook-signature": "v1,bad",
                    "webhook-timestamp": "1",
                },
                body=b"{}",
            )
        except SignatureError:
            raised = True
    _expect(raised, "SDK exception wrapped as SignatureError")

    print("\n[4] V3 envelope normalisation produces flat metadata")
    parsed_v3 = parsed  # reuse [1] for clarity
    _expect(
        {"trigger_slug", "trigger_id", "user_id", "toolkit_slug"}.issubset(
            parsed_v3.metadata.keys()
        ),
        "metadata contains the V3-flat keys",
    )
    _expect(
        parsed_v3.type == "composio.trigger.message",
        "type pulled from raw_payload",
    )

    if failures:
        print(f"\nWEBHOOK VERIFY SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nWEBHOOK VERIFY SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
