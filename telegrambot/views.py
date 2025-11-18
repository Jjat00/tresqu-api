from django.shortcuts import render
import json
import asyncio
import os
import threading
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.views.decorators.http import require_GET
from telegram import Update
from .bot import setup_bot
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

# Variable global para almacenar la aplicación del bot
application = None
# Variable para controlar el event loop
event_loop = None


def initialize_bot():
    """Inicializa el bot de Telegram si aún no está inicializado."""
    global application, event_loop
    if application is None:
        # Configurar el bot
        application = setup_bot()

        # Crear un event loop si no existe
        if event_loop is None:
            event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(event_loop)

        # Inicializar explícitamente la aplicación
        event_loop.run_until_complete(application.initialize())

        # Marcar que está inicializada para evitar reinicialización
        application._initialized = True

    return application


def process_update_async(app, update):
    """
    Procesa un update de Telegram de forma asíncrona en un thread separado.
    Esto evita bloquear el worker de Gunicorn.
    """
    try:
        # Crear un nuevo event loop para este thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Procesar el update
        try:
            loop.run_until_complete(app.process_update(update))
        except RuntimeError as e:
            if "not initialized" in str(e):
                # Si la aplicación no está inicializada, inicializar y volver a intentar
                loop.run_until_complete(app.initialize())
                loop.run_until_complete(app.process_update(update))
            else:
                raise
        finally:
            loop.close()

    except Exception as e:
        import traceback
        logger.error(f"Error procesando actualización en thread: {str(e)}")
        logger.error(traceback.format_exc())


@csrf_exempt
def telegram_webhook(request):
    """
    Endpoint para recibir actualizaciones de Telegram.
    Este endpoint debe configurarse en Telegram usando setWebhook.

    IMPORTANTE: Devuelve respuesta inmediatamente y procesa el update en segundo plano
    para evitar timeouts del worker de Gunicorn.
    """
    if request.method == 'GET':
        # Para diagnóstico, permitimos GET para verificar que el endpoint está funcionando
        return JsonResponse({
            "status": "ok",
            "message": "El webhook de Telegram está configurado. Envíe solicitudes POST a este endpoint."
        })

    if request.method == 'POST':
        try:
            logger.info("Recibiendo actualización de Telegram")
            logger.debug(f"Headers: {request.headers}")
            logger.debug(f"Body: {request.body.decode('utf-8')}")

            # Verificar si hay payload
            if not request.body:
                return JsonResponse({"status": "error", "message": "No se recibió payload"}, status=400)

            # Inicializar el bot (esto garantiza que la aplicación esté inicializada)
            app = initialize_bot()

            # Procesar el update de Telegram
            update_data = json.loads(request.body.decode('utf-8'))
            update = Update.de_json(update_data, app.bot)

            # Procesar el update en un thread separado para no bloquear el worker
            # Esto es crítico para mensajes de audio que pueden tardar más de 30 segundos
            thread = threading.Thread(
                target=process_update_async, args=(app, update), daemon=True)
            thread.start()

            # Devolver respuesta inmediatamente
            logger.info(
                "Update recibido y enviado a procesamiento en segundo plano")
            return JsonResponse({"status": "ok"})

        except json.JSONDecodeError as je:
            logger.error(f"Error decodificando JSON: {str(je)}")
            return JsonResponse({"status": "error", "message": f"Error decodificando JSON: {str(je)}"}, status=400)
        except Exception as e:
            import traceback
            logger.error(f"Error procesando actualización: {str(e)}")
            logger.error(traceback.format_exc())
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    else:
        return JsonResponse({"status": "error", "message": "Solo se permiten solicitudes GET y POST"}, status=405)


@csrf_exempt
def set_webhook(request):
    """
    Configura el webhook de Telegram con la URL del endpoint.
    Este endpoint debe llamarse manualmente para configurar el webhook.
    """
    # Permitir solicitudes GET por conveniencia durante pruebas
    if not settings.TELEGRAM_BOT_TOKEN:
        return JsonResponse({"status": "error", "message": "Token de bot no configurado"}, status=500)

    if not settings.TELEGRAM_WEBHOOK_URL:
        return JsonResponse({"status": "error", "message": "URL de webhook no configurada"}, status=500)

    try:
        # Inicializar el bot
        app = initialize_bot()

        # Configurar el webhook
        webhook_url = settings.TELEGRAM_WEBHOOK_URL

        # Usamos el mismo event loop
        global event_loop
        if event_loop is None:
            event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(event_loop)

        webhook_info = event_loop.run_until_complete(
            app.bot.set_webhook(webhook_url))

        if webhook_info:
            return JsonResponse({
                "status": "ok",
                "webhook_url": webhook_url,
                "message": "Webhook configurado exitosamente"
            })
        else:
            return JsonResponse({"status": "error", "message": "Falló al configurar el webhook"}, status=500)
    except Exception as e:
        import traceback
        print(f"Error configurando webhook: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@require_GET
def env_debug(request):
    """
    Endpoint para depuración de variables de entorno.
    Solo disponible en modo DEBUG.
    """
    if not settings.DEBUG:
        return JsonResponse({"status": "error", "message": "Endpoint solo disponible en modo DEBUG"}, status=403)

    # Mostrar información básica para depuración
    env_info = {
        "telegram_token": bool(settings.TELEGRAM_BOT_TOKEN) and f"{settings.TELEGRAM_BOT_TOKEN[:5]}...{settings.TELEGRAM_BOT_TOKEN[-5:]}" if settings.TELEGRAM_BOT_TOKEN else None,
        "webhook_url": settings.TELEGRAM_WEBHOOK_URL,
        "openai_key": bool(settings.OPENAI_API_KEY) and f"{settings.OPENAI_API_KEY[:5]}...{settings.OPENAI_API_KEY[-5:]}" if settings.OPENAI_API_KEY else None,
        "debug_mode": settings.DEBUG,
        "application_initialized": application is not None and hasattr(application, '_initialized'),
        "event_loop_initialized": event_loop is not None and not event_loop.is_closed(),
    }

    return JsonResponse({"status": "ok", "env_info": env_info})


@require_GET
def healthcheck(request):
    """
    Endpoint para verificar el estado del bot y la conectividad con OpenAI
    """
    health_status = {
        "status": "ok",
        "timestamp": timezone.now().isoformat(),
        "checks": {}
    }

    try:
        # Verificar configuración básica
        health_status["checks"]["telegram_token"] = bool(
            settings.TELEGRAM_BOT_TOKEN)
        health_status["checks"]["openai_key"] = bool(settings.OPENAI_API_KEY)
        health_status["checks"]["webhook_url"] = bool(
            settings.TELEGRAM_WEBHOOK_URL)

        # Verificar bot application
        health_status["checks"]["bot_initialized"] = application is not None
        health_status["checks"]["event_loop_active"] = event_loop is not None and not event_loop.is_closed()

        # Simple check de OpenAI (solo verificar que el cliente se puede crear)
        try:
            from telegrambot.services import openai_client
            health_status["checks"]["openai_client"] = True
        except Exception as e:
            health_status["checks"]["openai_client"] = False
            health_status["checks"]["openai_error"] = str(e)

        # Determinar estado general
        critical_checks = ["telegram_token", "openai_key", "bot_initialized"]
        if all(health_status["checks"].get(check, False) for check in critical_checks):
            health_status["status"] = "healthy"
        else:
            health_status["status"] = "degraded"

    except Exception as e:
        health_status["status"] = "error"
        health_status["error"] = str(e)

    status_code = 200 if health_status["status"] in ["ok", "healthy"] else 503
    return JsonResponse(health_status, status=status_code)
