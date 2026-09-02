"""Every proposed write in a turn must surface its own confirmation.

On 2026-09-02 "Compra 20 usd en Google y compra 20 usd en meta" produced two
decisions but only the last one got buttons; META was silently dropped.
"""
from __future__ import annotations

import json

from django.test import SimpleTestCase

from wallbit.confirmation_actions import (
    extract_pending_confirmation,
    extract_pending_confirmations,
)


class ToolMessage:  # duck-typed like langchain_core.messages.ToolMessage
    def __init__(self, content):
        self.content = content


class AIMessage:
    def __init__(self, content):
        self.content = content


def _pending(decision_id: int, symbol: str) -> ToolMessage:
    return ToolMessage(json.dumps({
        "ok": True,
        "requires_confirmation": True,
        "confirmation_id": decision_id,
        "two_step_required": False,
        "preview": {"summary": f"BUY {symbol} por USD 20.0"},
    }))


class ExtractPendingConfirmationsTests(SimpleTestCase):
    def test_returns_every_proposal_in_order(self):
        messages = [
            AIMessage("ok"),
            _pending(58, "META"),
            ToolMessage(json.dumps({"ok": True, "requires_confirmation": False})),
            _pending(59, "GOOG"),
            AIMessage("Confirma con los botones"),
        ]
        found = extract_pending_confirmations(messages)
        self.assertEqual([p["confirmation_id"] for p in found], [58, 59])
        self.assertEqual(extract_pending_confirmation(messages)["confirmation_id"], 59)

    def test_dedupes_by_confirmation_id_and_ignores_garbage(self):
        messages = [_pending(58, "META"), _pending(58, "META"), ToolMessage("not json"), ToolMessage("")]
        found = extract_pending_confirmations(messages)
        self.assertEqual(len(found), 1)

    def test_empty_when_nothing_pending(self):
        self.assertEqual(extract_pending_confirmations([AIMessage("hola")]), [])
        self.assertIsNone(extract_pending_confirmation([AIMessage("hola")]))
