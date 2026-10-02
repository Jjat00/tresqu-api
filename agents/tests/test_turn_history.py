"""Turnos en fila (``agents.turn_history``) e historial sin el mensaje actual.

    python -m agents.tests.test_turn_history

No toca OpenAI, Redis ni la base: el cliente Redis y el manager de
``Message`` se sustituyen por dobles en memoria.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import django

_FAILURES: list[str] = []


def _check(label: str, condition: bool) -> None:
    print(f"{'✅' if condition else '❌'} {label}")
    if not condition:
        _FAILURES.append(label)


def _setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()


# --- Dobles ------------------------------------------------------------------

class _FakeLock:
    """Lo justo de ``redis.asyncio.lock.Lock`` sobre un dict compartido."""

    def __init__(self, store: dict, name: str, blocking_timeout: float, sleep: float):
        self.store, self.name = store, name
        self.blocking_timeout, self.sleep = blocking_timeout, sleep
        self.token = object()

    async def acquire(self) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.blocking_timeout
        while True:
            if self.name not in self.store:
                self.store[self.name] = self.token
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(self.sleep)

    async def extend(self, *_args, **_kwargs) -> bool:
        return True

    async def release(self) -> None:
        if self.store.get(self.name) is not self.token:
            raise RuntimeError("no es nuestro")
        del self.store[self.name]


class _FakeRedis:
    store: dict = {}

    def lock(self, name, timeout, sleep, blocking_timeout, thread_local):
        return _FakeLock(self.store, name, blocking_timeout, sleep)

    async def aclose(self) -> None:
        pass


class _FakeQuerySet:
    """filter/exclude/order_by/slice sobre una lista, con la semántica que usa
    ``fetch_last_messages``."""

    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, **_kwargs):
        return self

    def exclude(self, **kwargs):
        def matches(row):
            for key, value in kwargs.items():
                field, _, op = key.partition("__")
                current = getattr(row, field)
                if op == "gt" and not current > value:
                    return False
                if not op and current != value:
                    return False
            return True

        return _FakeQuerySet(r for r in self.rows if not matches(r))

    def order_by(self, key):
        return _FakeQuerySet(sorted(self.rows, key=lambda r: r.created_at, reverse=key.startswith("-")))

    def __getitem__(self, item):
        return self.rows[item]


# --- Pruebas -----------------------------------------------------------------

async def _run_history() -> None:
    from telegrambot import utils as tg_utils
    from whatsappbot import utils as wa_utils

    def msg(pk, kind, text):
        return SimpleNamespace(id=pk, created_at=pk, message_type=kind, text=text)

    rows = [
        msg(1, "incoming", "48000 hamburguesa"),
        msg(2, "outgoing", "Registré 48.000 COP."),
        msg(3, "incoming", "12000 gaseosa"),
        msg(4, "incoming", "12000 préstamo amigo"),  # llegó mientras se atendía la gaseosa
        msg(5, "outgoing", "Registré 12.000 COP (gaseosa)."),
        msg(6, "incoming", "5000 mecato"),  # todavía en fila
    ]

    for name, module in (("telegram", tg_utils), ("whatsapp", wa_utils)):
        original = module.Message
        module.Message = SimpleNamespace(objects=_FakeQuerySet(rows))  # type: ignore[assignment]
        try:
            texts = [m.content async for m in module.fetch_last_messages(1, current_message_id=4)]
            legacy = [m.content async for m in module.fetch_last_messages(1)]
        finally:
            module.Message = original  # type: ignore[assignment]

        print(f"\n[{name}] historial del turno de '12000 préstamo amigo'")
        _check("no incluye el mensaje actual", "12000 préstamo amigo" not in texts)
        _check("incluye la respuesta a la gaseosa, guardada después", "Registré 12.000 COP (gaseosa)." in texts)
        _check("no incluye el entrante que aún espera turno", "5000 mecato" not in texts)
        _check(
            "conserva el orden cronológico",
            texts == ["48000 hamburguesa", "Registré 48.000 COP.", "12000 gaseosa", "Registré 12.000 COP (gaseosa)."],
        )
        _check("sin id actual se comporta como antes", len(legacy) == len(rows))


async def _run_lock() -> None:
    from agents import turn_history

    turn_history._redis_client = lambda: _FakeRedis()  # type: ignore[assignment]
    turn_history._POLL_SECONDS = 0.01

    print("\n[lock] Turnos en fila por usuario")
    order: list[str] = []

    async def turn(name: str, user_id: int, pause: float) -> None:
        async with turn_history.user_turn(user_id):
            order.append(f"{name}:in")
            await asyncio.sleep(pause)
            order.append(f"{name}:out")

    await asyncio.gather(turn("a", 1, 0.2), turn("b", 1, 0.0))
    _check("el segundo turno del mismo usuario espera al primero", order == ["a:in", "a:out", "b:in", "b:out"])

    order.clear()
    await asyncio.gather(turn("a", 1, 0.2), turn("c", 2, 0.0))
    _check("usuarios distintos no se esperan", order.index("c:out") < order.index("a:out"))

    order.clear()
    original_wait = turn_history._WAIT_SECONDS
    turn_history._WAIT_SECONDS = 0.05
    try:
        await asyncio.gather(turn("a", 3, 0.4), turn("b", 3, 0.0))
    finally:
        turn_history._WAIT_SECONDS = original_wait
    _check("si la espera se agota, el turno sigue igual", order.index("b:out") < order.index("a:out"))
    await asyncio.sleep(0.5)
    _check("y el que esperó no suelta el candado ajeno", _FakeRedis.store == {})

    order.clear()
    try:
        async with turn_history.user_turn(4):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    await asyncio.wait_for(turn("d", 4, 0.0), timeout=2)
    _check("un turno que revienta suelta el candado", order == ["d:in", "d:out"])

    async def _bad_return():
        async with turn_history.user_turn(5):
            return "temprano"

    _check("un return dentro del bloque también lo suelta", await _bad_return() == "temprano" and _FakeRedis.store == {})

    def _broken():
        raise ConnectionError("sin redis")

    turn_history._redis_client = _broken  # type: ignore[assignment]
    order.clear()
    await turn("e", 6, 0.0)
    _check("sin Redis el turno corre igual (fail-open)", order == ["e:in", "e:out"])


def main() -> int:
    _setup()
    asyncio.run(_run_history())
    asyncio.run(_run_lock())
    print()
    if _FAILURES:
        print(f"❌ {len(_FAILURES)} comprobaciones fallaron:")
        for failure in _FAILURES:
            print(f"   - {failure}")
        return 1
    print("✅ todas las comprobaciones pasaron")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
