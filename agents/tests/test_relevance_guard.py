"""Smoke test del guardrail de tema (``agents.relevance_guard``).

Cubre la máquina de estados completa sin gastar tokens: el clasificador se
sustituye por uno de mentira y la caché por LocMem, así que no hace falta ni
base de datos ni API key.

    python -m agents.tests.test_relevance_guard          # determinista
    python -m agents.tests.test_relevance_guard --live   # + clasificador real

La pasada ``--live`` sí llama a OpenAI (modelo nano) para comprobar la calidad
de la clasificación con frases reales.
"""

from __future__ import annotations

import asyncio
import os
import sys

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
                "LOCATION": "relevance-guard-test",
            }
        }
    ).enable()


async def _run_deterministic() -> None:
    from langchain_core.messages import AIMessage

    from agents import relevance_guard as guard

    real_classify = guard._classify
    verdicts: dict[str, dict] = {}

    async def fake_classify(text: str, history: list) -> dict:
        return verdicts.get(
            text, {"on_topic": False, "automated": False}
        )

    guard._classify = fake_classify  # type: ignore[assignment]

    scope = "test:1"
    history = [AIMessage(content="Registré tu gasto de 20.000 COP en café.")]

    print("\n[1] Atajos locales (sin clasificador)")
    _check("un monto pasa directo", (await guard.check_relevance(scope, "gasté 30k en mercado", [])).allow)
    _check("un comando pasa directo", (await guard.check_relevance(scope, "/perfil", [])).allow)
    _check(
        "respuesta corta a un turno de Tresqu pasa directo",
        (await guard.check_relevance(scope, "sí, dale", history)).allow,
    )

    print("\n[2] Fuera de tema: aviso una vez, luego silencio")
    first = await guard.check_relevance(scope, "hazme un fibonacci en python", [])
    _check("el primero no ejecuta el supervisor", not first.allow)
    _check("el primero responde el aviso fijo", first.reply == guard.OFF_TOPIC_REPLY)

    second = await guard.check_relevance(scope, "y ahora explícame quicksort", [])
    _check("el segundo es silencio absoluto", not second.allow and second.silent)

    third = await guard.check_relevance(scope, "¿quién ganó el partido?", [])
    _check("el tercero sigue en silencio", not third.allow and third.silent)

    print("\n[3] Volver al tema levanta el silencio")
    verdicts["cuánto llevo gastado"] = {"on_topic": True, "automated": False}
    back = await guard.check_relevance(scope, "cuánto llevo gastado", [])
    _check("un mensaje de finanzas vuelve a pasar", back.allow)

    after = await guard.check_relevance(scope, "cuéntame un chiste", [])
    _check(
        "y la racha quedó reiniciada (vuelve a haber aviso)",
        after.reply == guard.OFF_TOPIC_REPLY,
    )

    print("\n[4] Mensaje automático: silencio inmediato, sin dejar sordo a nadie")
    scope_bot = "test:2"
    verdicts["Este es un mensaje automático, no responda"] = {
        "on_topic": False,
        "automated": True,
    }
    auto = await guard.check_relevance(scope_bot, "Este es un mensaje automático, no responda", [])
    _check("no responde nada al primer intento (sin aviso)", not auto.allow and auto.silent)
    _check(
        "no aplica mute ciego: la capa de ráfaga lo deja pasar",
        guard.check_flood(scope_bot, "gasté 50k en café") is None,
    )
    _check(
        "y un mensaje de finanzas sigue siendo atendido",
        (await guard.check_relevance(scope_bot, "gasté 50k en café", [])).allow,
    )

    print("\n[5] Ráfaga: corta el bucle sin leer el contenido")
    scope_flood = "test:3"
    for i in range(guard.BURST_MAX):
        _ = guard.check_flood(scope_flood, f"mensaje distinto {i}")
    burst = guard.check_flood(scope_flood, "mensaje distinto final")
    _check("supera el umbral y bloquea", burst is not None and burst.reason == "flood")
    _check("el bloqueo por ráfaga es silencioso", burst is not None and burst.silent)

    print("\n[6] Eco: el mismo texto repetido corta")
    scope_echo = "test:4"
    repeated = "hola"
    results = [guard.check_flood(scope_echo, repeated) for _ in range(guard.ECHO_MAX)]
    _check("el último repetido bloquea", results[-1] is not None and results[-1].reason == "echo")

    print("\n[7] Fail-open: si el clasificador revienta, el mensaje pasa")
    guard._classify = real_classify  # type: ignore[assignment]
    original_factory = guard._classifier

    def _broken_factory():
        raise RuntimeError("sin API")

    guard._classifier = _broken_factory  # type: ignore[assignment]
    try:
        fallback = await guard._classify("lo que sea", [])
    finally:
        guard._classifier = original_factory  # type: ignore[assignment]
    _check("un fallo del clasificador deja pasar el mensaje", fallback["on_topic"] is True)


async def _run_context() -> None:
    """El contexto que se le pasa al clasificador."""
    from langchain_core.messages import AIMessage, HumanMessage

    from agents import relevance_guard as guard

    print("\n[9] Contexto de la conversación")
    history = [
        HumanMessage(content="compré una moto usada el finde"),
        AIMessage(content="Listo, la registré como transporte: 8.000.000 COP."),
    ]
    context = guard._recent_context(history)
    _check("incluye lo que dijo el usuario", "Usuario: compré una moto usada el finde" in context)
    _check("incluye lo que dijo Tresqu", "Tresqu: Listo, la registré" in context)
    _check("historial vacío no revienta", guard._recent_context([]) == "")

    _check(
        "reconoce que el turno previo lo cerró Tresqu",
        guard._last_turn_is_assistant(history) is True,
    )
    _check(
        "y que un turno del usuario no es una pregunta pendiente",
        guard._last_turn_is_assistant(history + [HumanMessage(content="ah bueno")]) is False,
    )

    # Una respuesta corta a una pregunta de Tresqu pasa sin gastar clasificador;
    # la misma frase sin esa pregunta detrás, no.
    async def _off(text, hist):
        return {"on_topic": False, "automated": False}

    guard._classify = _off  # type: ignore[assignment]
    _check(
        "'la primera' tras una pregunta de Tresqu pasa",
        (await guard.check_relevance("test:ctx1", "la primera", history)).allow,
    )
    _check(
        "'la primera' sin conversación detrás, no",
        not (await guard.check_relevance("test:ctx2", "la primera", [])).allow,
    )


async def _run_integration() -> None:
    """El cableado real: ``process_message`` corta antes de construir el supervisor."""
    from types import SimpleNamespace

    from agents import relevance_guard as guard
    from agents import risk_profiler_service, services

    built = {"n": 0}

    def _never_build(*args, **kwargs):
        built["n"] += 1
        raise AssertionError("el supervisor no debía construirse")

    async def _no_session(user_id):
        return False

    async def _off_topic(text: str, history: list) -> dict:
        return {"on_topic": False, "automated": False}

    services.build_supervisor = _never_build  # type: ignore[assignment]
    risk_profiler_service.is_session_active = _no_session  # type: ignore[assignment]
    guard._classify = _off_topic  # type: ignore[assignment]

    user = SimpleNamespace(id=4242, timezone="UTC")

    print("\n[8] process_message extremo a extremo")
    first = await services.process_message(
        user=user, raw_text="dame la receta del ajiaco", channel="whatsapp", history=[]
    )
    _check(
        "primer off-topic: aviso fijo y ni un token de supervisor",
        first.text == guard.OFF_TOPIC_REPLY and not first.silent and built["n"] == 0,
    )

    second = await services.process_message(
        user=user, raw_text="y la del sancocho", channel="whatsapp", history=[]
    )
    _check(
        "segundo off-topic: respuesta silenciosa",
        second.silent and second.text == "" and built["n"] == 0,
    )

    # Un mensaje del dominio sí debe llegar al supervisor: el falso lo delata
    # incrementando el contador (y su excepción la absorbe process_message).
    await services.process_message(
        user=user, raw_text="gasté 32.000 en el mercado", channel="whatsapp", history=[]
    )
    _check("un gasto sí llega al supervisor y levanta el silencio", built["n"] == 1)


async def _run_live() -> None:
    """Comprueba el clasificador real contra frases de referencia."""
    import importlib

    from agents import relevance_guard as guard

    importlib.reload(guard)

    cases = [
        ("gasté 45 mil en el almuerzo", True),
        ("¿cuánto llevo gastado este mes?", True),
        ("¿cómo va NVDA?", True),
        ("hola, buenas", True),
        ("gracias!", True),
        ("¿qué puedes hacer?", True),
        ("escríbeme una función de fibonacci en python", False),
        ("¿quién ganó las elecciones?", False),
        ("¿me recomiendas una serie para ver hoy?", False),
        ("Estimado cliente, este es un mensaje automático. No responda a este número.", False),
    ]
    print("\n[live] clasificador real, mensaje suelto")
    for text, expected in cases:
        result = await guard._classify(text, [])
        ok = result["on_topic"] == expected
        _check(f"{'on ' if expected else 'off'} | {text[:48]!r} -> {result}", ok)

    # Lo que de verdad importa: mensajes que solos no dicen nada y en contexto sí.
    from langchain_core.messages import AIMessage, HumanMessage

    moto = [
        HumanMessage(content="compré una moto usada el fin de semana"),
        AIMessage(content="Listo, la registré como transporte: 8.000.000 COP el 2026-08-24."),
    ]
    pregunta = [
        HumanMessage(content="anota 60 mil de la salida de anoche"),
        AIMessage(content="¿La registro en Restaurantes o en Entretenimiento?"),
    ]
    nvda = [
        HumanMessage(content="cómo va NVDA?"),
        AIMessage(content="NVDA cerró en 178,2 USD, un 3,1 % abajo en la semana."),
    ]

    contextual = [
        ("era de segunda, por eso me salió tan barata", True, moto),
        ("la segunda", True, pregunta),
        ("y eso cómo me deja el mes?", True, moto),
        ("y cuánto llevo perdido ahí?", True, nvda),
        # Respuestas que solas no significan nada y que el hilo vuelve válidas.
        ("no, el otro", True, pregunta),
        ("fue el martes por la tarde", True, pregunta),
        ("ninguna de las dos, mejor déjalo así", True, pregunta),
        # Y el control opuesto: el contexto no debe volverse permisivo con un
        # tema nuevo ajeno a las finanzas.
        ("oye y de paso, ¿me pasas la receta del ajiaco?", False, moto),
        ("mejor cuéntame un chiste", False, pregunta),
        ("hazme un script de python para eso", False, pregunta),
    ]
    print("\n[live] clasificador real, con la conversación detrás")
    for text, expected, hist in contextual:
        result = await guard._classify(text, hist)
        ok = result["on_topic"] == expected
        _check(f"{'on ' if expected else 'off'} | {text[:44]!r} -> {result}", ok)


def main() -> int:
    _setup()
    asyncio.run(_run_deterministic())
    asyncio.run(_run_context())
    asyncio.run(_run_integration())
    if "--live" in sys.argv:
        asyncio.run(_run_live())
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
