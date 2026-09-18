"""``WallbitClient`` retry policy: reads may retry, writes never do.

Regression tests for the 2026-09-02 incident: ``POST /trades`` timed out (Wallbit
answers after the fill, 15–28 s) and the client resent it four times, placing
four real orders. A write that gets no answer must surface as
``WallbitUncertainError`` after exactly one attempt so the caller reconciles
instead of resending.
"""
from __future__ import annotations

from unittest import mock

import httpx
from django.test import SimpleTestCase

from wallbit.client import (
    WRITE_TIMEOUT,
    WallbitClient,
    WallbitError,
    WallbitUncertainError,
    WallbitValidationError,
)

BASE = "https://wallbit.test"


def _client_with(handler) -> WallbitClient:
    client = WallbitClient("key", base_url=BASE, max_attempts=4)
    client._client = httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE)
    return client


class WritePolicyTests(SimpleTestCase):
    def setUp(self):
        sleeper = mock.patch("wallbit.client.time.sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_post_timeout_is_one_attempt_and_uncertain(self):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ReadTimeout("The read operation timed out", request=request)

        with _client_with(handler) as client:
            with self.assertRaises(WallbitUncertainError):
                client.post("/trades", json={"symbol": "SPCX", "amount": 20.0})

        self.assertEqual(len(calls), 1, "a timed-out POST must never be resent")

    def test_post_uses_write_timeout(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.extensions.get("timeout") or {})
            return httpx.Response(200, json={"data": {"uuid": "tx-1"}})

        with _client_with(handler) as client:
            client.post("/trades", json={})

        self.assertEqual(seen.get("read"), WRITE_TIMEOUT.read)

    def test_post_5xx_is_one_attempt_and_uncertain(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(502, json={"message": "bad gateway"})

        with _client_with(handler) as client:
            with self.assertRaises(WallbitUncertainError) as ctx:
                client.post("/trades", json={})

        self.assertEqual(len(calls), 1)
        self.assertEqual(ctx.exception.status, 502)

    def test_post_4xx_is_definitive_rejection(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                422, json={"message": "The amount is insufficient to cover the transaction fee."}
            )

        with _client_with(handler) as client:
            with self.assertRaises(WallbitValidationError) as ctx:
                client.post("/trades", json={})

        self.assertEqual(len(calls), 1)
        self.assertNotIsInstance(ctx.exception, WallbitUncertainError)
        self.assertIn("insufficient", str(ctx.exception))

    def test_patch_timeout_is_uncertain_too(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        with _client_with(handler) as client:
            with self.assertRaises(WallbitUncertainError):
                client.patch("/cards/abc/status", json={"status": "SUSPENDED"})

    def test_get_timeout_retries_then_plain_error(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ReadTimeout("timed out", request=request)

        with _client_with(handler) as client:
            with self.assertRaises(WallbitError) as ctx:
                client.get("/assets/SPCX")

        self.assertEqual(len(calls), 4, "reads keep the bounded retry policy")
        self.assertNotIsInstance(ctx.exception, WallbitUncertainError)

    def test_get_5xx_then_200_recovers(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(500, json={"message": "oops"})
            return httpx.Response(200, json={"data": {"price": 140.69}})

        with _client_with(handler) as client:
            resp = client.get("/assets/SPCX")

        self.assertEqual(len(calls), 2)
        self.assertEqual(resp.data["data"]["price"], 140.69)
