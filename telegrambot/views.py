from django.shortcuts import render
import json
import asyncio
import os
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.views.decorators.http import require_GET
from telegram import Update
from .bot import setup_bot

# Variable global para almacenar la aplicación del bot
application = None
# Variable para controlar el event loop
event_loop = None


def initialize_bot():
    """Inicializa el bot de Telegram si aún no está inicializado."""
    global application, event_loop
    if application is None:
        application = setup_bot()

        # Importante: inicializar la aplicación antes de usarla
        # Creamos un nuevo event loop en lugar de usar asyncio.run
        if event_loop is None:
            event_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(event_loop)

        # Inicializamos la aplicación
        event_loop.run_until_complete(application.initialize())

    return application


@csrf_exempt
def telegram_webhook(request):
    """
    Endpoint para recibir actualizaciones de Telegram.
    Este endpoint debe configurarse en Telegram usando setWebhook.
    """
    if request.method == 'GET':
        # Para diagnóstico, permitimos GET para verificar que el endpoint está funcionando
        return JsonResponse({
            "status": "ok",
            "message": "El webhook de Telegram está configurado. Envíe solicitudes POST a este endpoint."
        })

    if request.method == 'POST':
        try:
            print("Recibiendo actualización de Telegram")
            print(f"Headers: {request.headers}")
            print(f"Body: {request.body.decode('utf-8')}")

            print('⚠️⚠️', request.body)

            # Verificar si hay payload
            if not request.body:
                return JsonResponse({"status": "error", "message": "No se recibió payload"}, status=400)

            # Inicializar el bot
            app = initialize_bot()

            # Procesar el update de Telegram
            update_data = json.loads(request.body.decode('utf-8'))
            update = Update.de_json(update_data, app.bot)

            # Usamos el mismo event loop para procesar la actualización
            global event_loop
            if event_loop is None:
                event_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(event_loop)

            # Procesamos la actualización en el event loop existente
            event_loop.run_until_complete(app.process_update(update))

            return JsonResponse({"status": "ok"})
        except json.JSONDecodeError as je:
            return JsonResponse({"status": "error", "message": f"Error decodificando JSON: {str(je)}"}, status=400)
        except Exception as e:
            import traceback
            print(f"Error procesando actualización: {str(e)}")
            print(traceback.format_exc())
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
        "application_initialized": application is not None,
        "event_loop_initialized": event_loop is not None,
    }

    return JsonResponse({"status": "ok", "env_info": env_info})
