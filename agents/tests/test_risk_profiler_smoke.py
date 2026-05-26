"""Smoke test for the risk profiler graph.

Not part of the regular test suite — run with:

    docker-compose -f docker-compose.dev.yml exec worker python -m agents.tests.test_risk_profiler_smoke

Picks an arbitrary existing user (the first one that has a User row) and
walks through the full Q&A using canned answers. Verifies:
- ``start_session`` returns step 1
- ``resume_session`` returns step 2..5 then ``done=True`` with final_text
- ``RiskProfile`` is persisted with non-default values
- ``RiskAssessment`` row is created with ``triggered_by="chat_qa"``
"""

from __future__ import annotations

import asyncio
import os
import sys

import django


def _setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


CANNED_ANSWERS = [
    "Más de 10 años, no necesito ese dinero pronto.",
    "c) Mantengo y espero recuperación. No me asustan las caídas si entiendo el activo.",
    "Aproximadamente un 30% líquido, el resto puedo dejarlo invertido.",
    "8/10. Conozco ETFs, bonos y acciones, los uso desde hace 5 años.",
    "B) La apuesta con expectativa de 6M vale más que los 5M garantizados; soy joven y puedo aceptar la volatilidad.",
]


async def _run() -> int:
    from asgiref.sync import sync_to_async

    from agents import risk_profiler_service
    from agents.models import RiskAssessment, RiskProfile
    from users.models import User

    user = await sync_to_async(lambda: User.objects.first())()
    if user is None:
        print("FAIL: no users exist in the database")
        return 1
    print(f"Using user id={user.id} username={getattr(user, 'username', '?')}")

    # Wipe existing profile so we can verify writes happen.
    await sync_to_async(lambda: RiskProfile.objects.filter(user=user).delete())()

    print("→ start_session")
    step = await risk_profiler_service.start_session(user.id, channel="cli")
    assert not step.get("done"), f"unexpected done on first step: {step}"
    print(f"  step1: {step['question'][:80]}...")

    for i, answer in enumerate(CANNED_ANSWERS, start=1):
        print(f"→ resume_session #{i} with {answer[:40]!r}...")
        step = await risk_profiler_service.resume_session(user.id, answer)
        if step.get("done"):
            print(f"  final_text:\n{step['final_text']}\n")
            break
        print(f"  next question (step {step.get('step')}/{step.get('total')}): {step['question'][:80]}...")
    else:
        print("FAIL: graph did not complete after 5 answers")
        return 1

    profile = await sync_to_async(lambda: RiskProfile.objects.filter(user=user).first())()
    if profile is None:
        print("FAIL: RiskProfile not persisted")
        return 1
    print(
        f"OK: RiskProfile saved — tolerance={profile.tolerance} score={profile.score} "
        f"confidence={profile.confidence} dimensions={profile.dimensions}"
    )

    assessments_count = await sync_to_async(
        lambda: RiskAssessment.objects.filter(profile=profile, triggered_by="chat_qa").count()
    )()
    if assessments_count < 1:
        print("FAIL: no RiskAssessment(chat_qa) row created")
        return 1
    print(f"OK: {assessments_count} RiskAssessment(chat_qa) row(s) created")

    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(asyncio.run(_run()))
