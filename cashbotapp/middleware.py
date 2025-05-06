import logging
import json
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class AuthLoggingMiddleware(MiddlewareMixin):
    """
    Middleware para registrar información de autenticación para diagnóstico
    """

    def process_request(self, request):
        """Procesa la solicitud entrante para registrar información de autenticación"""
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')

        # Registrar cabeceras de autorización (sin exponer el token completo)
        if auth_header:
            parts = auth_header.split()
            if len(parts) == 2:
                token_type, token = parts
                logger.info(
                    f"Request a {request.path}, con tipo de token: {token_type}")
                # Registrar solo información parcial para no exponer datos sensibles
                if len(token) > 10:
                    logger.info(f"Token parcial: ...{token[-10:]}")
            else:
                logger.warning(
                    f"Formato incorrecto en cabecera de autorización: {auth_header[:10]}...")

        return None
