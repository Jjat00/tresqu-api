import logging
import base64
from datetime import datetime, timezone as dt_timezone
from email.utils import parsedate_to_datetime

from django.conf import settings
from django.utils import timezone
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from .encryption import decrypt_token
from .oauth import refresh_access_token
from .models import GmailWatch

logger = logging.getLogger(__name__)


def get_gmail_service(google_account):
    """
    Construye y devuelve un servicio autenticado de Gmail API.
    Refresca el token si está expirado.
    """
    try:
        # Verificar si el token necesita ser refrescado
        if google_account.token_expiry and google_account.token_expiry <= timezone.now():
            logger.info(f"Token expirado para {google_account.google_email}, refrescando...")
            refresh_access_token(google_account)
            google_account.refresh_from_db()

        access_token = decrypt_token(bytes(google_account.access_token_encrypted))
        refresh_token = decrypt_token(bytes(google_account.refresh_token_encrypted))

        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
        )

        service = build('gmail', 'v1', credentials=credentials, cache_discovery=False)
        return service

    except Exception as e:
        logger.error(f"Error creando servicio de Gmail para {google_account.google_email}: {e}")
        raise


def setup_watch(google_account):
    """
    Configura la suscripción push de Gmail usando Pub/Sub.
    Crea o actualiza el registro GmailWatch asociado.
    """
    try:
        service = get_gmail_service(google_account)

        request_body = {
            'topicName': settings.GOOGLE_PUBSUB_TOPIC,
            'labelIds': ['INBOX'],
        }

        response = service.users().watch(userId='me', body=request_body).execute()

        history_id = response.get('historyId')
        expiration = response.get('expiration')

        # Convertir expiration de milisegundos a datetime
        watch_expiration = None
        if expiration:
            watch_expiration = datetime.fromtimestamp(
                int(expiration) / 1000, tz=dt_timezone.utc
            )

        # Crear o actualizar el registro de watch
        watch, created = GmailWatch.objects.update_or_create(
            google_account=google_account,
            defaults={
                'history_id': history_id,
                'watch_expiration': watch_expiration,
                'is_active': True,
            }
        )

        logger.info(
            f"Watch configurado para {google_account.google_email}: "
            f"history_id={history_id}, expiration={watch_expiration}"
        )
        return watch

    except Exception as e:
        logger.error(f"Error configurando watch para {google_account.google_email}: {e}")
        raise


def stop_watch(google_account):
    """
    Detiene la suscripción push de Gmail.
    """
    try:
        service = get_gmail_service(google_account)
        service.users().stop(userId='me').execute()

        # Desactivar el registro de watch
        try:
            watch = google_account.watch
            watch.is_active = False
            watch.save(update_fields=['is_active', 'updated_at'])
        except GmailWatch.DoesNotExist:
            pass

        logger.info(f"Watch detenido para {google_account.google_email}")

    except Exception as e:
        logger.error(f"Error deteniendo watch para {google_account.google_email}: {e}")
        raise


def get_history(google_account, start_history_id):
    """
    Obtiene el historial de mensajes nuevos desde un history_id dado.

    Returns:
        list: Lista de message_ids de mensajes añadidos
    """
    try:
        service = get_gmail_service(google_account)
        message_ids = []

        # Paginar los resultados del historial
        request = service.users().history().list(
            userId='me',
            startHistoryId=start_history_id,
            historyTypes=['messageAdded'],
            labelId='INBOX',
        )

        while request is not None:
            response = request.execute()
            history_records = response.get('history', [])

            for record in history_records:
                messages_added = record.get('messagesAdded', [])
                for msg_added in messages_added:
                    msg = msg_added.get('message', {})
                    msg_id = msg.get('id')
                    if msg_id:
                        message_ids.append(msg_id)

            request = service.users().history().list_next(request, response)

        logger.info(
            f"Historial obtenido para {google_account.google_email}: "
            f"{len(message_ids)} mensajes nuevos desde history_id={start_history_id}"
        )
        return message_ids

    except Exception as e:
        error_str = str(e)
        # Si el history_id es inválido, retornar lista vacía
        if '404' in error_str or 'notFound' in error_str:
            logger.warning(
                f"History ID {start_history_id} no encontrado para "
                f"{google_account.google_email}. Es posible que sea muy antiguo."
            )
            return []
        logger.error(f"Error obteniendo historial para {google_account.google_email}: {e}")
        raise


def get_message(service, message_id):
    """
    Obtiene un mensaje completo de Gmail por su ID.
    """
    try:
        message = service.users().messages().get(
            userId='me',
            id=message_id,
            format='full'
        ).execute()
        return message
    except Exception as e:
        logger.error(f"Error obteniendo mensaje {message_id}: {e}")
        raise


def extract_email_text(message) -> dict:
    """
    Extrae información relevante de un mensaje de Gmail.

    Returns:
        dict con: subject, sender, date, body (truncado a 3000 caracteres)
    """
    try:
        payload = message.get('payload', {})
        headers = payload.get('headers', [])

        # Extraer headers relevantes
        subject = ''
        sender = ''
        date_str = ''

        for header in headers:
            name = header.get('name', '').lower()
            value = header.get('value', '')
            if name == 'subject':
                subject = value
            elif name == 'from':
                sender = value
            elif name == 'date':
                date_str = value

        # Parsear fecha
        received_at = None
        if date_str:
            try:
                received_at = parsedate_to_datetime(date_str)
            except Exception:
                logger.warning(f"No se pudo parsear la fecha del email: {date_str}")

        # Extraer cuerpo del mensaje
        body = _extract_body(payload)

        # Truncar el cuerpo a 3000 caracteres
        if body and len(body) > 3000:
            body = body[:3000] + '...'

        return {
            'subject': subject,
            'sender': sender,
            'date': received_at,
            'body': body or '',
        }

    except Exception as e:
        logger.error(f"Error extrayendo texto del email: {e}")
        return {
            'subject': '',
            'sender': '',
            'date': None,
            'body': '',
        }


def _extract_body(payload) -> str:
    """
    Extrae el cuerpo del mensaje desde el payload, priorizando texto plano.
    """
    mime_type = payload.get('mimeType', '')

    # Si es texto plano o HTML directo
    if mime_type in ('text/plain', 'text/html'):
        data = payload.get('body', {}).get('data', '')
        if data:
            decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
            # Si es HTML, eliminar tags básicos para obtener texto legible
            if mime_type == 'text/html':
                import re
                decoded = re.sub(r'<style[^>]*>.*?</style>', '', decoded, flags=re.DOTALL)
                decoded = re.sub(r'<script[^>]*>.*?</script>', '', decoded, flags=re.DOTALL)
                decoded = re.sub(r'<[^>]+>', ' ', decoded)
                decoded = re.sub(r'\s+', ' ', decoded).strip()
            return decoded

    # Si es multipart, buscar recursivamente
    parts = payload.get('parts', [])
    plain_text = ''
    html_text = ''

    for part in parts:
        part_mime = part.get('mimeType', '')
        if part_mime == 'text/plain':
            data = part.get('body', {}).get('data', '')
            if data:
                plain_text = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        elif part_mime == 'text/html':
            data = part.get('body', {}).get('data', '')
            if data:
                decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
                import re
                decoded = re.sub(r'<style[^>]*>.*?</style>', '', decoded, flags=re.DOTALL)
                decoded = re.sub(r'<script[^>]*>.*?</script>', '', decoded, flags=re.DOTALL)
                decoded = re.sub(r'<[^>]+>', ' ', decoded)
                html_text = re.sub(r'\s+', ' ', decoded).strip()
        elif 'multipart' in part_mime:
            # Recursión para partes multipart anidadas
            nested = _extract_body(part)
            if nested:
                if not plain_text:
                    plain_text = nested

    # Priorizar texto plano sobre HTML
    return plain_text or html_text
