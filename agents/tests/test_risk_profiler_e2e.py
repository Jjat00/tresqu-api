"""End-to-end smoke test for the risk profiler integration in process_message.

Validates:
1. ``/perfil`` shortcut starts a fresh session.
2. Subsequent messages are intercepted while a session is active.
3. After completion, the next message routes back to the supervisor normally.

Run with:

    docker-compose -f docker-compose.dev.yml exec worker python -m agents.tests.test_risk_profiler_e2e
"""

from __future__ import annotations

import asyncio
import os
import sys

import django


def _setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


CANNED = [
    "menos de 1 año, lo necesito pronto",
    "a) vendo todo, no aguanto perder",
    "100% líquido, todo a la mano",
    "2/10, soy principiante total",
    "A) los 5M garantizados, prefiero seguridad",
]


async def _run() -> int:
    from asgiref.sync import sync_to_async

    from agents.models import RiskProfile
    from agents.services import process_message
    from users.models import User

    user = await sync_to_async(lambda: User.objects.first())()
    print(f"User: id={user.id}")

    await sync_to_async(lambda: RiskProfile.objects.filter(user=user).delete())()

    print("\n[1] /perfil shortcut")
    response = await process_message(user=user, raw_text="/perfil", channel="cli", history=[])
    print(f"    response.text = {response.text[:100]}...")
    assert "perfil de inversión" in response.text.lower(), response.text

    print("\n[2] feeding canned answers as regular messages")
    for i, ans in enumerate(CANNED, start=1):
        response = await process_message(user=user, raw_text=ans, channel="cli", history=[])
        print(f"    turn {i}: {response.text[:80]}...")
        if "perfil de inversión está listo" in response.text.lower():
            print(f"    → completed at turn {i}")
            break
    else:
        print("FAIL: never reached completion")
        return 1

    profile = await sync_to_async(lambda: RiskProfile.objects.filter(user=user).first())()
    print(
        f"\n[3] RiskProfile persisted: tolerance={profile.tolerance} score={profile.score} "
        f"dims={profile.dimensions}"
    )
    assert profile.tolerance == "conservative", f"expected conservative, got {profile.tolerance}"

    print("\n[4] post-session message routes to supervisor (not intercepted)")
    response = await process_message(
        user=user, raw_text="hola, cuánto gasté este mes?", channel="cli", history=[]
    )
    print(f"    response.text = {response.text[:150]}...")
    # Should NOT be a Q&A question — should be normal supervisor answer.
    assert "vamos a armar" not in response.text.lower(), "session should be over but graph started again"

    print("\n✅ ALL E2E ASSERTIONS PASSED")
    return 0


if __name__ == "__main__":
    _setup()
    sys.exit(asyncio.run(_run()))
