import logging
import time
import json
from django.db import connection
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger('cashbotapp')


class DatabaseConnectionMiddleware(MiddlewareMixin):
    """Middleware para gestionar y monitorear conexiones a la base de datos"""

    def process_request(self, request):
        # Almacenar el tiempo de inicio para el cálculo de duración
        request.db_start_time = time.time()
        return None

    def process_response(self, request, response):
        # Si la solicitud es rápida, podemos cerrar la conexión para liberar recursos
        if hasattr(request, 'db_start_time'):
            duration = time.time() - request.db_start_time
            # Si la duración es mayor a 1 segundo, registramos como consulta lenta
            if duration > 1.0:
                logger.warning(
                    f"Consulta lenta detectada: {duration:.2f}s - {request.path}")

            # Para solicitudes API que no sean de streaming, cerramos la conexión
            if request.path.startswith('/api/') and not request.path.startswith('/api/stream/'):
                connection.close()
                logger.debug(
                    f"Conexión cerrada después de solicitud: {request.path}")

        return response


class AuthLoggingMiddleware(MiddlewareMixin):
    """Middleware para registrar intentos de autenticación"""

    def process_request(self, request):
        # Registrar información de autenticación
        if request.path.startswith('/api/token/') or request.path.startswith('/api/user/login/'):
            logger.info(
                f"Intento de autenticación desde {request.META.get('REMOTE_ADDR')}")
        return None

    def process_response(self, request, response):
        # Registrar resultados de autenticación
        if (request.path.startswith('/api/token/') or request.path.startswith('/api/user/login/')) and response.status_code != 200:
            logger.warning(
                f"Fallo de autenticación desde {request.META.get('REMOTE_ADDR')}")
        return response
