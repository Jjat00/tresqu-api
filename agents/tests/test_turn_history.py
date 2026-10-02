"""Historial sin el mensaje actual y turnos en fila (``agents.turn_history``).

    python -m agents.tests.test_turn_history

No toca OpenAI ni la base: la caché se sustituye por LocMem.
"""

from __future__ import annotations

import asyncio
import os

import django

_FAILURES: list[str] = []


def _check(label: str, condition: bool) -> None:
    print(f"{'✅' if condition else '❌'} {label}")
    if not condition:
        _FAILURES.append(label)


def _setup() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")
    django.setup()
    from django.test.utils import override_settings

    override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "turn-history-test",
            }
        }
    ).enable()


def _run_history() -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from agents.turn_history import without_current_message

    print("\n[1] El mensaje actual sale del historial")
    previous = [HumanMessage(content="48000 hamburguesa"), AIMessage(content="Registré 48.000 COP.")]
    stored = previous + [HumanMessage(content="35000 plan de datos")]
    _check(
        "quita la copia guardada del mensaje que se procesa",
        without_current_message(stored, "35000 plan de datos") == previous,
    )
    _check(
        "ignora diferencias de espacios",
        without_current_message(stored, "  35000  plan de datos ") == previous,
    )
    _check(
        "no toca nada si el mensaje no está guardado (chat web)",
        without_current_message(previous, "35000 plan de datos") == previous,
    )
    _check("historial vacío no revienta", without_current_message([], "hola") == [])

    print("\n[2] WhatsApp antepone el mensaje citado")
    quoted = '[Respondiendo al mensaje anterior: "Compra detectada"]\nera mercado'
    stored = previous + [HumanMessage(content="era mercado")]
    _check(
        "reconoce la copia aunque el texto procesado lleve el contexto citado",
        without_current_message(stored, quoted) == previous,
    )

    print("\n[3] Dos mensajes casi juntos")
    # El segundo turno espera al primero; cuando carga el historial, la
    # respuesta al primero quedó guardada DESPUÉS de su propio mensaje.
    stored = [
        HumanMessage(content="12000 gaseosa"),
        HumanMessage(content="12000 préstamo amigo"),
        AIMessage(content="Registré 12.000 COP en Alimentación (gaseosa)."),
    ]
    cleaned = without_current_message(stored, "12000 préstamo amigo")
    _check(
        "quita la copia aunque no sea el último mensaje",
        [m.content for m in cleaned] == ["12000 gaseosa", "Registré 12.000 COP en Alimentación (gaseosa)."],
    )
    repeated = [HumanMessage(content="5000 mecato"), AIMessage(content="Listo."), HumanMessage(content="5000 mecato")]
    _check(
        "si el usuario repite el mismo texto, solo quita la copia más reciente",
        [m.content for m in without_current_message(repeated, "5000 mecato")] == ["5000 mecato", "Listo."],
    )


async def _run_lock() -> None:
    from agents import turn_history

    print("\n[4] Turnos en fila por usuario")
    order: list[str] = []

    async def turn(name: str, user_id: int, pause: float) -> None:
        async with turn_history.user_turn(user_id):
            order.append(f"{name}:in")
            await asyncio.sleep(pause)
            order.append(f"{name}:out")

    await asyncio.gather(turn("a", 1, 0.3), turn("b", 1, 0.0))
    _check("el segundo turno del mismo usuario espera al primero", order == ["a:in", "a:out", "b:in", "b:out"])

    order.clear()
    await asyncio.gather(turn("a", 1, 0.3), turn("c", 2, 0.0))
    _check("usuarios distintos no se esperan", order.index("c:out") < order.index("a:out"))

    order.clear()
    original_wait = turn_history._WAIT_SECONDS
    turn_history._WAIT_SECONDS = 0.2
    try:
        await asyncio.gather(turn("a", 3, 0.8), turn("b", 3, 0.0))
    finally:
        turn_history._WAIT_SECONDS = original_wait
    _check("si la espera se agota, el turno sigue igual", order.index("b:out") < order.index("a:out"))

    order.clear()
    try:
        async with turn_history.user_turn(4):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    await asyncio.wait_for(turn("d", 4, 0.0), timeout=2)
    _check("un turno que revienta suelta el candado", order == ["d:in", "d:out"])


def main() -> int:
    _setup()
    _run_history()
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
