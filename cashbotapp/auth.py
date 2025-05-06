import logging
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from django.apps import apps

logger = logging.getLogger(__name__)


def custom_user_authentication_rule(user):
    """
    Regla personalizada de autenticación que siempre devuelve True
    (usamos nuestro propio modelo de usuario, no el de Django)
    """
    return True


class CustomJWTAuthentication(JWTAuthentication):
    """
    Autenticación JWT personalizada para usar nuestro modelo de usuario
    """

    def get_user(self, validated_token):
        """
        Obtiene el usuario a partir del token validado
        """
        try:
            user_id = validated_token.get('user_id')
            logger.info(f"Intentando obtener usuario con ID: {user_id}")

            if not user_id:
                logger.error("No se encontró 'user_id' en el token")
                raise AuthenticationFailed({
                    'detail': 'Token inválido: sin user_id',
                    'code': 'token_no_user_id'
                })

            # Usar apps.get_model para obtener el modelo directamente
            User = apps.get_model('users', 'User')
            try:
                user = User.objects.get(id=user_id)
                logger.info(f"Usuario encontrado: {user}")

                # Asegurar que el usuario tiene los atributos necesarios
                # para trabajar con rest_framework
                if not hasattr(user, 'is_authenticated'):
                    user.is_authenticated = True

                return user
            except User.DoesNotExist:
                logger.error(f"No se encontró el usuario con ID {user_id}")
                raise AuthenticationFailed({
                    'detail': 'User not found',
                    'code': 'user_not_found'
                })

        except (KeyError, TypeError) as e:
            logger.error(f"Error al procesar el token: {str(e)}")
            raise AuthenticationFailed({
                'detail': f'Token inválido: {str(e)}',
                'code': 'invalid_token'
            })
