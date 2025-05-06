import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Manejador de excepciones personalizado para REST Framework
    """
    # Registrar la excepción para diagnóstico
    logger.error(
        f"Excepción en API: {str(exc)}, Tipo: {type(exc)}, Contexto: {context}")

    # Primero, manejo estándar de REST framework
    response = exception_handler(exc, context)

    # Si no hay una respuesta, crear una personalizada
    if response is None:
        # Manejar excepciones JWT
        if isinstance(exc, (InvalidToken, TokenError)):
            logger.error(f"Error de Token JWT: {str(exc)}")
            return Response(
                {"detail": f"Token inválido: {str(exc)}",
                 "code": "invalid_token"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Manejar otras excepciones no capturadas
        logger.error(f"Excepción no manejada: {str(exc)}")
        return Response(
            {"detail": "Error del servidor", "code": "server_error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # Si es una excepción relacionada con el usuario
    if hasattr(exc, 'detail') and 'User not found' in str(exc.detail):
        logger.error(f"Error de usuario no encontrado: {str(exc)}")
        response.data = {
            "detail": "Usuario no encontrado",
            "code": "user_not_found",
            "error_info": str(exc)
        }

    return response
