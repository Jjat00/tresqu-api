import logging
import json

from django.conf import settings
from django.core import signing
from django.utils import timezone
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from .encryption import encrypt_token, decrypt_token

logger = logging.getLogger(__name__)

# Scopes requeridos para leer correos de Gmail
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
GMAIL_OAUTH_STATE_SALT = 'gmailbot.oauth.state'
GMAIL_OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60


def _get_client_config():
    """Construye la configuración del cliente OAuth2 desde settings."""
    return {
        'web': {
            'client_id': settings.GOOGLE_CLIENT_ID,
            'client_secret': settings.GOOGLE_CLIENT_SECRET,
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': [settings.GOOGLE_REDIRECT_URI],
        }
    }


def generate_auth_url(user) -> str:
    """
    Genera la URL de autorización de Google OAuth2.
    Incluye un state firmado para identificar al usuario en el callback.
    """
    try:
        flow = Flow.from_client_config(
            _get_client_config(),
            scopes=GMAIL_SCOPES,
            redirect_uri=settings.GOOGLE_REDIRECT_URI,
        )

        state = generate_oauth_state(user)

        auth_url, _ = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            state=state,
            include_granted_scopes='true',
        )

        logger.info(f"URL de autorización generada para usuario {user.id}")
        return auth_url

    except Exception as e:
        logger.error(f"Error generando URL de autorización: {e}")
        raise


def generate_oauth_state(user) -> str:
    """
    Genera un parámetro OAuth state no manipulable para enlazar el callback
    con el usuario que inició la conexión.
    """
    return signing.dumps({'user_id': user.id}, salt=GMAIL_OAUTH_STATE_SALT)


def parse_oauth_state(state: str) -> int:
    """
    Valida el parámetro OAuth state y retorna el ID del usuario iniciador.
    Lanza BadSignature o SignatureExpired si el state no es confiable.
    """
    data = signing.loads(
        state,
        salt=GMAIL_OAUTH_STATE_SALT,
        max_age=GMAIL_OAUTH_STATE_MAX_AGE_SECONDS,
    )
    user_id = data.get('user_id')
    if user_id is None:
        raise signing.BadSignature('OAuth state sin user_id')
    return int(user_id)


def exchange_code(code: str) -> dict:
    """
    Intercambia el código de autorización por tokens de acceso y refresco.
    Usa requests directamente para evitar errores de scope mismatch
    (Google puede devolver scopes adicionales como openid, userinfo.email, etc.)

    Returns:
        dict con: access_token, refresh_token, token_expiry, scopes
    """
    import requests as http_requests
    from datetime import datetime, timedelta

    try:
        response = http_requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'code': code,
                'client_id': settings.GOOGLE_CLIENT_ID,
                'client_secret': settings.GOOGLE_CLIENT_SECRET,
                'redirect_uri': settings.GOOGLE_REDIRECT_URI,
                'grant_type': 'authorization_code',
            },
        )

        if response.status_code != 200:
            logger.error(f"Error en token exchange: {response.status_code} - {response.text}")
            raise Exception(f"Token exchange failed: {response.text}")

        token_data = response.json()

        # Calcular expiración del token
        expires_in = token_data.get('expires_in', 3600)
        token_expiry = timezone.now() + timedelta(seconds=expires_in)

        result = {
            'access_token': token_data['access_token'],
            'refresh_token': token_data.get('refresh_token', ''),
            'token_expiry': token_expiry,
            'scopes': token_data.get('scope', ''),
        }

        logger.info("Código de autorización intercambiado exitosamente")
        return result

    except Exception as e:
        logger.error(f"Error intercambiando código de autorización: {e}")
        raise


def refresh_access_token(google_account) -> str:
    """
    Refresca el token de acceso de una cuenta de Google.
    Actualiza el token cifrado en la base de datos.

    Returns:
        str: Nuevo token de acceso
    """
    try:
        refresh_token = decrypt_token(bytes(google_account.refresh_token_encrypted))

        credentials = Credentials(
            token=decrypt_token(bytes(google_account.access_token_encrypted)),
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
        )

        # Refrescar el token
        credentials.refresh(Request())

        # Actualizar en la base de datos
        google_account.access_token_encrypted = encrypt_token(credentials.token)
        google_account.token_expiry = credentials.expiry
        google_account.save(update_fields=['access_token_encrypted', 'token_expiry', 'updated_at'])

        logger.info(f"Token de acceso refrescado para {google_account.google_email}")
        return credentials.token

    except Exception as e:
        logger.error(f"Error refrescando token de acceso para {google_account.google_email}: {e}")
        raise


def revoke_token(google_account):
    """
    Revoca el token de acceso en Google.
    """
    try:
        import requests

        access_token = decrypt_token(bytes(google_account.access_token_encrypted))
        response = requests.post(
            'https://oauth2.googleapis.com/revoke',
            params={'token': access_token},
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
        )

        if response.status_code == 200:
            logger.info(f"Token revocado exitosamente para {google_account.google_email}")
        else:
            logger.warning(
                f"Error revocando token para {google_account.google_email}: "
                f"status={response.status_code}, body={response.text}"
            )

    except Exception as e:
        logger.error(f"Error revocando token para {google_account.google_email}: {e}")
        # No relanzar la excepción; la revocación no es crítica
