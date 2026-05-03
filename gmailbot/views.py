import base64
import json
import logging

from django.conf import settings
from django.core import signing
from django.http import HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from users.models import User

from .encryption import encrypt_token
from .gmail_service import setup_watch, stop_watch
from .models import GoogleAccount, GmailWatch, ProcessedEmail
from .oauth import exchange_code, generate_auth_url, parse_oauth_state, revoke_token
from .serializers import GoogleAccountStatusSerializer, ProcessedEmailSerializer

logger = logging.getLogger(__name__)


class GmailOAuthURLView(APIView):
    """
    GET: Genera y devuelve la URL de autorización de Google OAuth2.
    Requiere autenticación JWT.
    """

    def get(self, request):
        try:
            user = request.user
            auth_url = generate_auth_url(user)
            return Response({'auth_url': auth_url}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error generando URL de OAuth: {e}")
            return Response(
                {'error': 'Error generando URL de autorización'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GmailOAuthCallbackView(APIView):
    """
    GET: Maneja el callback de Google OAuth2.
    Guarda los tokens, configura el watch y redirige al frontend.
    No requiere autenticación JWT (usa state param para identificar al usuario).
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        frontend_url = settings.FRONTEND_URL.rstrip('/')
        code = request.query_params.get('code')
        state = request.query_params.get('state')
        error = request.query_params.get('error')

        profile_path = f"{frontend_url}/dashboard/profile"

        if error:
            logger.error(f"Error en callback de OAuth: {error}")
            return HttpResponseRedirect(f"{profile_path}?tab=connections&gmail=error&reason={error}")

        if not code or not state:
            logger.error("Callback de OAuth sin código o state")
            return HttpResponseRedirect(f"{profile_path}?tab=connections&gmail=error&reason=missing_params")

        try:
            # Recuperar el usuario desde el state firmado generado al iniciar OAuth.
            user_id = parse_oauth_state(state)
            user = User.objects.get(id=user_id)

            # Intercambiar el código por tokens
            tokens = exchange_code(code)

            # Obtener el email del usuario de Google
            from googleapiclient.discovery import build
            from google.oauth2.credentials import Credentials

            credentials = Credentials(token=tokens['access_token'])
            gmail_service = build('gmail', 'v1', credentials=credentials, cache_discovery=False)
            profile = gmail_service.users().getProfile(userId='me').execute()
            google_email = profile.get('emailAddress', '')

            # Cifrar los tokens
            encrypted_access = encrypt_token(tokens['access_token'])
            encrypted_refresh = encrypt_token(tokens['refresh_token'])

            # Crear o actualizar la cuenta de Google
            google_account, created = GoogleAccount.objects.update_or_create(
                user=user,
                defaults={
                    'google_email': google_email,
                    'access_token_encrypted': encrypted_access,
                    'refresh_token_encrypted': encrypted_refresh,
                    'token_expiry': tokens.get('token_expiry'),
                    'scopes': tokens.get('scopes', 'https://www.googleapis.com/auth/gmail.readonly'),
                    'is_active': True,
                }
            )

            # Configurar el watch de Gmail para recibir notificaciones push
            try:
                setup_watch(google_account)
            except Exception as e:
                logger.error(f"Error configurando watch de Gmail: {e}")
                # No fallar el flujo completo si el watch falla

            logger.info(f"Cuenta de Gmail conectada exitosamente: {google_email} para usuario {user.id}")
            return HttpResponseRedirect(f"{profile_path}?tab=connections&gmail=connected")

        except User.DoesNotExist:
            logger.error(f"Usuario no encontrado con state={state}")
            return HttpResponseRedirect(f"{profile_path}?tab=connections&gmail=error&reason=user_not_found")
        except (ValueError, TypeError):
            logger.error("State de OAuth inválido")
            return HttpResponseRedirect(f"{profile_path}?tab=connections&gmail=error&reason=invalid_state")
        except (signing.BadSignature, signing.SignatureExpired):
            logger.error("State de OAuth inválido o expirado")
            return HttpResponseRedirect(f"{profile_path}?tab=connections&gmail=error&reason=invalid_state")
        except Exception as e:
            logger.error(f"Error en callback de OAuth: {e}")
            return HttpResponseRedirect(f"{profile_path}?tab=connections&gmail=error&reason=exchange_failed")


class GmailDisconnectView(APIView):
    """
    POST: Desconecta la cuenta de Gmail del usuario.
    Revoca el token, detiene el watch y elimina la cuenta.
    """

    def post(self, request):
        try:
            user = request.user
            google_account = GoogleAccount.objects.filter(user=user).first()

            if not google_account:
                return Response(
                    {'error': 'No hay cuenta de Gmail conectada'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Detener el watch
            try:
                stop_watch(google_account)
            except Exception as e:
                logger.error(f"Error deteniendo watch: {e}")

            # Revocar el token
            try:
                revoke_token(google_account)
            except Exception as e:
                logger.error(f"Error revocando token: {e}")

            # Eliminar la cuenta de Google
            google_email = google_account.google_email
            google_account.delete()

            logger.info(f"Cuenta de Gmail desconectada: {google_email} para usuario {user.id}")
            return Response(
                {'message': f'Cuenta {google_email} desconectada exitosamente'},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error desconectando cuenta de Gmail: {e}")
            return Response(
                {'error': 'Error desconectando cuenta de Gmail'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GmailStatusView(APIView):
    """
    GET: Devuelve el estado de la conexión de Gmail y estadísticas.
    """

    def get(self, request):
        try:
            user = request.user
            google_account = GoogleAccount.objects.filter(user=user, is_active=True).first()

            if not google_account:
                data = {
                    'connected': False,
                    'google_email': '',
                    'connected_at': None,
                    'watch_active': False,
                    'watch_expires': None,
                    'total_emails_processed': 0,
                    'total_purchases_detected': 0,
                    'pending_categorization': 0,
                    'last_sync': None,
                }
                serializer = GoogleAccountStatusSerializer(data)
                return Response(serializer.data, status=status.HTTP_200_OK)

            # Obtener datos del watch
            watch_active = False
            watch_expires = None
            try:
                watch = google_account.watch
                watch_active = watch.is_active
                watch_expires = watch.watch_expiration
            except GmailWatch.DoesNotExist:
                pass

            # Estadísticas
            total_emails = google_account.processed_emails.count()
            total_purchases = google_account.processed_emails.filter(is_purchase=True).count()
            pending = google_account.processed_emails.filter(awaiting_categorization=True).count()

            # Última sincronización
            last_email = google_account.processed_emails.order_by('-created_at').first()
            last_sync = last_email.created_at if last_email else google_account.created_at

            data = {
                'connected': True,
                'google_email': google_account.google_email,
                'connected_at': google_account.created_at,
                'watch_active': watch_active,
                'watch_expires': watch_expires,
                'total_emails_processed': total_emails,
                'total_purchases_detected': total_purchases,
                'pending_categorization': pending,
                'last_sync': last_sync,
            }

            serializer = GoogleAccountStatusSerializer(data)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error obteniendo estado de Gmail: {e}")
            return Response(
                {'error': 'Error obteniendo estado de Gmail'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ProcessedEmailListView(ListAPIView):
    """
    GET: Lista paginada de emails procesados del usuario.
    """
    serializer_class = ProcessedEmailSerializer

    def get_queryset(self):
        user = self.request.user
        google_account = GoogleAccount.objects.filter(user=user).first()
        if not google_account:
            return ProcessedEmail.objects.none()
        return google_account.processed_emails.select_related('expense', 'expense__user_expense_category').all()


class GmailSyncView(APIView):
    """
    POST: Dispara una sincronización manual de Gmail.
    """

    def post(self, request):
        try:
            user = request.user
            google_account = GoogleAccount.objects.filter(user=user, is_active=True).first()

            if not google_account:
                return Response(
                    {'error': 'No hay cuenta de Gmail conectada'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Verificar que hay un watch activo
            try:
                watch = google_account.watch
            except GmailWatch.DoesNotExist:
                return Response(
                    {'error': 'No hay watch activo. Reconecta tu cuenta de Gmail.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not watch.history_id:
                return Response(
                    {'error': 'No hay history_id disponible para sincronizar'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Ejecutar la sincronización
            from .email_processor import process_history_update
            process_history_update(google_account, watch.history_id)

            # Renovar el watch si es necesario
            try:
                setup_watch(google_account)
            except Exception as e:
                logger.warning(f"Error renovando watch durante sync manual: {e}")

            return Response(
                {'message': 'Sincronización completada'},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error en sincronización manual de Gmail: {e}")
            return Response(
                {'error': 'Error durante la sincronización'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@method_decorator(csrf_exempt, name='dispatch')
class GmailWebhookView(APIView):
    """
    POST: Recibe notificaciones push de Google Pub/Sub cuando hay nuevos emails.
    No requiere autenticación (viene de Google Cloud).
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            # Decodificar el mensaje de Pub/Sub
            envelope = request.data
            if not envelope:
                logger.warning("Webhook de Gmail recibido sin datos")
                return Response(status=status.HTTP_400_BAD_REQUEST)

            # El formato de Pub/Sub envía los datos en envelope.message.data (base64)
            pubsub_message = envelope.get('message', {})
            data_b64 = pubsub_message.get('data', '')

            if not data_b64:
                logger.warning("Webhook de Gmail sin datos en message.data")
                return Response(status=status.HTTP_400_BAD_REQUEST)

            # Decodificar base64
            decoded_data = base64.b64decode(data_b64).decode('utf-8')
            notification = json.loads(decoded_data)

            email_address = notification.get('emailAddress', '')
            history_id = notification.get('historyId', '')

            logger.info(f"Notificación de Gmail recibida: email={email_address}, history_id={history_id}")

            if not email_address or not history_id:
                logger.warning("Notificación de Gmail sin emailAddress o historyId")
                return Response(status=status.HTTP_200_OK)

            # Buscar la cuenta de Google asociada
            google_account = GoogleAccount.objects.filter(
                google_email=email_address,
                is_active=True,
            ).first()

            if not google_account:
                logger.warning(f"No se encontró cuenta de Google para {email_address}")
                return Response(status=status.HTTP_200_OK)

            # Procesar la actualización del historial
            from .email_processor import process_history_update
            process_history_update(google_account, history_id)

            return Response(status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error procesando webhook de Gmail: {e}")
            # Siempre retornar 200 para evitar que Pub/Sub reintente
            return Response(status=status.HTTP_200_OK)
