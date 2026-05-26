"""Smoke test for interrupt handling during an active risk-profiler Q&A.

Validates the three classification branches:
- ``qa_answer``: normal advancement
- ``other_intent``: supervisor processes the message, Q&A stays paused,
  resume hint is appended.
- ``cancel``: session ends, user can start a fresh one later.

Run with:
    docker-compose -f docker-compose.dev.yml exec worker python -m agents.tests.test_risk_profiler_interrupts
"""

from __future__ import annotations

import asyncio
import os
import sys

import django


def _setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


async def _run() -> int:
    from asgiref.sync import sync_to_async

    from agents import risk_profiler_service
    from agents.models import RiskProfile
    from agents.services import RESUME_PROFILE_HINT, process_message
    from users.models import User

    user = await sync_to_async(lambda: User.objects.first())()
    print(f"User id={user.id}")
    await sync_to_async(lambda: RiskProfile.objects.filter(user=user).delete())()
    await risk_profiler_service.abandon_session(user.id)

    print("\n[1] /perfil")
    r = await process_message(user=user, raw_text="/perfil", channel="cli", history=[])
    print(f"    {r.text[:100]}...")
    assert "perfil de inversión" in r.text.lower()

    print("\n[2] answer #1 (qa_answer): 'entre 3 y 5 años'")
    r = await process_message(user=user, raw_text="entre 3 y 5 años", channel="cli", history=[])
    print(f"    {r.text[:100]}...")
    assert "imagina" in r.text.lower() or "caída" in r.text.lower(), "expected question 2 about 20% drop"

    print("\n[3] interrupt with other_intent: 'gasté 10k en café'")
    r = await process_message(user=user, raw_text="gasté 10k en café", channel="cli", history=[])
    print(f"    {r.text[:200]}...")
    assert RESUME_PROFILE_HINT.strip() in r.text, "expected resume-profile hint at end of response"

    print("\n[4] verify Q&A is still paused (next message goes to Q&A again)")
    pending = await risk_profiler_service.get_pending_question(user.id)
    assert pending is not None, "Q&A should still be paused"
    assert "caída" in pending.lower() or "20%" in pending, "should still be on question 2"
    print(f"    pending question: {pending[:80]}...")

    print("\n[5] answer #2 (qa_answer): 'b, vendo una parte'")
    r = await process_message(user=user, raw_text="b, vendo una parte", channel="cli", history=[])
    print(f"    {r.text[:100]}...")
    assert "líquido" in r.text.lower(), "expected question 3 about liquidity"

    print("\n[6] cancel: 'cancelar, después lo hago'")
    r = await process_message(user=user, raw_text="cancelar, después lo hago", channel="cli", history=[])
    print(f"    {r.text[:120]}...")
    assert "después" in r.text.lower() or "/perfil" in r.text.lower(), "expected cancel acknowledgement"

    print("\n[7] post-cancel: message routes to supervisor")
    active = await risk_profiler_service.is_session_active(user.id)
    assert not active, "session should be terminated after cancel"
    r = await process_message(user=user, raw_text="hola, cuánto gasté este mes?", channel="cli", history=[])
    print(f"    {r.text[:120]}...")
    assert RESUME_PROFILE_HINT.strip() not in r.text, "no resume hint after cancel"

    print("\n✅ INTERRUPT FLOW ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(asyncio.run(_run()))
