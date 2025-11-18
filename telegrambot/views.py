from django.shortcuts import render
import json
import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.views.decorators.http import require_GET
from telegram import Update
from .bot import setup_bot
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

# Thread pool para procesar updates de forma asíncrona
# Cada thread creará su propia instancia del bot para evitar conflictos de event loops
executor = ThreadPoolExecutor(
    max_workers=10, thread_name_prefix="telegram_worker")


def process_update_sync(update_data):
    """
    Procesa un update de Telegram de forma síncrona pero sin bloquear el worker principal.

    Esta función se ejecuta en un thread del pool, con su propio event loop y su propia instancia del bot.
    Esto evita conflictos de event loops entre threads.
    """
    try:
        # Crear un nuevo event loop para este thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Crear una nueva instancia del bot para este thread
            # Esto evita problemas de objetos asyncio vinculados a diferentes event loops
            thread_app = setup_bot()

            # Inicializar la aplicación en este event loop
            loop.run_until_complete(thread_app.initialize())

            # Reconstruir el objeto Update desde el JSON
            update = Update.de_json(update_data, thread_app.bot)

            # Procesar el update
            loop.run_until_complete(thread_app.process_update(update))

            logger.info(
                f"Update procesado exitosamente en thread {threading.current_thread().name}")

        finally:
            # Limpiar tareas pendientes antes de cerrar
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(
                        *pending, return_exceptions=True))
            except Exception as cleanup_error:
                logger.warning(f"Error limpiando tareas: {cleanup_error}")
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

            # Procesar el update de Telegram
            update_data = json.loads(request.body.decode('utf-8'))

            # Enviar el update al thread pool para procesamiento en segundo plano
            # Pasamos el JSON directamente para evitar problemas de serialización entre threads
            # Esto es crítico para mensajes de audio que pueden tardar más de 30 segundos
            executor.submit(process_update_sync, update_data)

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
        # Crear un bot temporal para configurar el webhook
        temp_app = setup_bot()

        # Configurar el webhook
        webhook_url = settings.TELEGRAM_WEBHOOK_URL

        # Crear un event loop temporal
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Inicializar la aplicación
            loop.run_until_complete(temp_app.initialize())

            # Configurar el webhook
            webhook_info = loop.run_until_complete(
                temp_app.bot.set_webhook(webhook_url))

            if webhook_info:
                return JsonResponse({
                    "status": "ok",
                    "webhook_url": webhook_url,
                    "message": "Webhook configurado exitosamente"
                })
            else:
                return JsonResponse({"status": "error", "message": "Falló al configurar el webhook"}, status=500)
        finally:
            loop.close()

    except Exception as e:
        import traceback
        logger.error(f"Error configurando webhook: {str(e)}")
        logger.error(traceback.format_exc())
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
        "architecture": "thread_pool_per_request",
        "thread_pool_workers": executor._max_workers,
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

        # Verificar thread pool
        health_status["checks"]["thread_pool_active"] = not executor._shutdown
        health_status["checks"]["thread_pool_workers"] = executor._max_workers

        # Simple check de OpenAI (solo verificar que el cliente se puede crear)
        try:
            from telegrambot.services import openai_client
            health_status["checks"]["openai_client"] = True
        except Exception as e:
            health_status["checks"]["openai_client"] = False
            health_status["checks"]["openai_error"] = str(e)

        # Determinar estado general
        critical_checks = ["telegram_token",
                           "openai_key", "thread_pool_active"]
        if all(health_status["checks"].get(check, False) for check in critical_checks):
            health_status["status"] = "healthy"
        else:
            health_status["status"] = "degraded"

    except Exception as e:
        health_status["status"] = "error"
        health_status["error"] = str(e)

    status_code = 200 if health_status["status"] in ["ok", "healthy"] else 503
    return JsonResponse(health_status, status=status_code)
