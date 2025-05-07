import logging
import json
from django.utils.deprecation import MiddlewareMixin
from django.db import connections, InterfaceError

logger = logging.getLogger(__name__)


class DatabaseConnectionMiddleware:
    """
    Middleware para manejar conexiones a la base de datos
    - Maneja errores de conexión cerrada o expirada
    - Ya no cierra conexiones al final de cada solicitud porque ahora usamos pool
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Código que se ejecuta antes de la vista
        response = None
        try:
            response = self.get_response(request)
        except InterfaceError as e:
            logger.warning(f"Error de interfaz con la base de datos: {e}")
            # No cerramos las conexiones manualmente, dejamos que el pool lo maneje
            # Re-intentar solicitud una vez
            try:
                response = self.get_response(request)
            except Exception as e2:
                logger.error(f"Error en segundo intento de respuesta: {e2}")
                raise
        except Exception as e:
            # Registrar otras excepciones, pero no hacer nada especial
            logger.error(f"Error en middleware: {e}")
            raise

        return response


class AuthLoggingMiddleware:
    """
    Middleware para registrar información de autenticación
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Código que se ejecuta antes de la vista
        response = self.get_response(request)

        # Código que se ejecuta después de la vista
        if hasattr(request, 'user') and request.user.is_authenticated:
            logger.info(
                f"Usuario autenticado: {request.user.username} (ID: {request.user.id})")

        return response
