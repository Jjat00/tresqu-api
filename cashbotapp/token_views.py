"""
Refresh de JWT resuelto contra nuestro modelo `users.User`.

Este proyecto no define `AUTH_USER_MODEL`: los tokens los emite
`RefreshToken.for_user(users.User)` y `CustomJWTAuthentication` los resuelve
contra `users.User`. Pero `TokenRefreshView` de simplejwt (>= 5.4) busca al
dueño del refresh token con `get_user_model()`, es decir, en `auth_user`.
Resultado: el refresh de serie lanzaba `auth.User.DoesNotExist` (HTTP 500)
para cualquier usuario cuyo id no coincidiera por casualidad con una fila de
`auth_user`, y el dashboard se quedaba sin datos sin llegar a cerrar sesión.
"""
from typing import Any

from django.apps import apps
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.views import TokenRefreshView


class UserTokenRefreshSerializer(TokenRefreshSerializer):
    """Igual que el de simplejwt, pero el dueño del token se busca en `users.User`."""

    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        refresh = self.token_class(attrs["refresh"])

        user_id = refresh.payload.get(api_settings.USER_ID_CLAIM)
        User = apps.get_model("users", "User")
        user = (
            User.objects.filter(**{api_settings.USER_ID_FIELD: user_id}).first()
            if user_id
            else None
        )
        if user is None:
            # 401 con `token_not_valid`: el cliente lo interpreta como sesión
            # muerta y vuelve al login (un 500 lo trataría como fallo transitorio).
            raise InvalidToken({"detail": "User not found", "code": "user_not_found"})

        if not api_settings.USER_AUTHENTICATION_RULE(user):
            raise AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            )

        data = {"access": str(refresh.access_token)}

        if api_settings.ROTATE_REFRESH_TOKENS:
            if api_settings.BLACKLIST_AFTER_ROTATION:
                try:
                    refresh.blacklist()
                except AttributeError:
                    # Sin la app token_blacklist no existe `blacklist()`.
                    pass

            refresh.set_jti()
            refresh.set_exp()
            refresh.set_iat()
            refresh.outstand()

            data["refresh"] = str(refresh)

        return data


class UserTokenRefreshView(TokenRefreshView):
    serializer_class = UserTokenRefreshSerializer
