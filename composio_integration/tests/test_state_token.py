"""Smoke test for the signed state-token used in the OAuth callback.

Run with:

    python -m composio_integration.tests.test_state_token

Cases covered:
- encode / decode roundtrip exposes uid, tk, nonce
- expired token (TTL=600s) raises StateTokenError("state token expired")
- signature mismatch (SECRET_KEY changed) raises StateTokenError
- nonce consume: first call returns True, second returns False (anti-replay)
- empty nonce input returns False without touching the cache
- JWT missing a required field raises StateTokenError

No DB writes happen here — state_token only touches the Django cache.
The cache backend in dev is the DB table ``django_cache_table``; we
clean the keys we add at the end of the run.
"""

from __future__ import annotations

import os
import sys


def _setup() -> None:
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


def _run() -> int:
    import time

    import jwt
    from django.conf import settings
    from django.core.cache import cache

    from composio_integration import state_token as st
    from composio_integration.exceptions import StateTokenError

    failures: list[str] = []

    def _expect(condition: bool, label: str) -> None:
        marker = "OK" if condition else "FAIL"
        print(f"    [{marker}] {label}")
        if not condition:
            failures.append(label)

    print("\n[1] encode/decode roundtrip")
    token = st.encode_state(42, "gmail")
    payload = st.decode_state(token)
    _expect(payload["uid"] == 42, "uid == 42")
    _expect(payload["tk"] == "gmail", "tk == 'gmail'")
    _expect(bool(payload.get("nonce")), "nonce populated")

    print("\n[2] expired token raises StateTokenError")
    # Mintamos directamente un JWT con exp ya en el pasado para no
    # depender de monkey-patches de time.time (PyJWT puede haber
    # cacheado la referencia).
    expired_token = jwt.encode(
        {
            "uid": 7,
            "tk": "gmail",
            "nonce": "expired-test",
            "exp": int(time.time()) - 100,
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    raised = False
    try:
        st.decode_state(expired_token)
    except StateTokenError as exc:
        raised = "expired" in str(exc).lower()
    _expect(raised, "decode raises StateTokenError('expired')")

    print("\n[3] signature mismatch raises StateTokenError")
    original_secret = settings.SECRET_KEY
    token_signed = st.encode_state(99, "gmail")
    try:
        settings.SECRET_KEY = original_secret + "-mutated"
        raised = False
        try:
            st.decode_state(token_signed)
        except StateTokenError:
            raised = True
        _expect(raised, "decode raises StateTokenError on bad signature")
    finally:
        settings.SECRET_KEY = original_secret

    print("\n[4] consume_nonce: single use only")
    # Forzar cache local en memoria para no depender de django_cache_table
    # (que puede no estar creada en el ambiente del test).
    from django.core.cache import caches
    from django.test.utils import override_settings

    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "composio-tests",
            }
        }
    ):
        # Limpiar el cache antes y refrescar la referencia del módulo
        caches["default"].clear()
        fresh_nonce = "smoke-nonce-" + os.urandom(4).hex()
        first = st.consume_nonce(fresh_nonce)
        second = st.consume_nonce(fresh_nonce)
        _expect(first is True, "first consume returns True")
        _expect(second is False, "replayed consume returns False")

        print("\n[5] consume_nonce(''): returns False, cache untouched")
        sentinel_key = "composio:nonce:"
        pre = caches["default"].get(sentinel_key)
        result = st.consume_nonce("")
        post = caches["default"].get(sentinel_key)
        _expect(result is False, "empty nonce returns False")
        _expect(pre == post, "cache untouched for empty nonce")

    print("\n[6] decode rejects payload missing 'uid'")
    bad_token = jwt.encode(
        {
            "tk": "gmail",
            "nonce": "abc",
            "exp": int(time.time()) + 600,
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    raised = False
    try:
        st.decode_state(bad_token)
    except StateTokenError as exc:
        raised = "uid" in str(exc)
    _expect(raised, "decode raises StateTokenError on missing uid")

    if failures:
        print(f"\nSTATE TOKEN SMOKE FAILED — {len(failures)} assertion(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSTATE TOKEN SMOKE PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(_run())
