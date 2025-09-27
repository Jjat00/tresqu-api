from django.shortcuts import render
from django.utils import timezone
import uuid
import datetime
import hashlib
import hmac
import time
import json
import requests
from django.conf import settings
from django.http import JsonResponse

# Create your views here.

from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, SubscriptionPlan, Subscription, Organization, OrganizationMembership, OrganizationInvitation, TelegramVerification, TrackingLink
from .serializers import (
    UserSerializer,
    SubscriptionPlanSerializer,
    SubscriptionSerializer,
    OrganizationSerializer,
    OrganizationDetailSerializer,
    OrganizationCreateSerializer,
    OrganizationMembershipSerializer,
    OrganizationInvitationSerializer,
    TrackingLinkSerializer,
    TrackingLinkCreateSerializer,
    TrackingLinkStatsSerializer
)

# Importar información de monedas
from telegrambot.currencies import COMMON_CURRENCIES, ISO_4217_CODES


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filtrar para que los usuarios solo puedan ver su propia información"""
        if not self.request.user.is_authenticated:
            return User.objects.none()
        return User.objects.filter(id=self.request.user.id)

    @action(detail=False, methods=['get'])
    def available_currencies(self, request):
        """
        Obtiene las monedas disponibles en el sistema
        GET /api/users/available_currencies/

        Retorna tanto las monedas comunes (con banderas) como todas las monedas ISO 4217 válidas
        """
        return Response({
            'common_currencies': COMMON_CURRENCIES,
            'all_currencies': [
                {'code': code, 'name': name}
                for code, name in ISO_4217_CODES.items()
            ]
        })

    @action(detail=True, methods=['get'])
    def subscription_history(self, request, pk=None):
        user = self.get_object()
        subscriptions = user.subscription_history.all().order_by('-start_date')
        serializer = SubscriptionSerializer(subscriptions, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def organizations(self, request, pk=None):
        """Lista las organizaciones a las que pertenece el usuario"""
        user = self.get_object()
        memberships = OrganizationMembership.objects.filter(user=user)
        organizations = [membership.organization for membership in memberships]
        serializer = OrganizationSerializer(organizations, many=True)
        return Response(serializer.data)


class SubscriptionPlanViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = SubscriptionPlan.objects.filter(is_active=True)
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [AllowAny]


class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filtrar suscripciones por usuario autenticado"""
        if not self.request.user.is_authenticated:
            return Subscription.objects.none()
        return Subscription.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        """Asegurar que las nuevas suscripciones se asignen al usuario autenticado"""
        serializer.save(user=self.request.user)


class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all()
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return OrganizationCreateSerializer
        elif self.action == 'retrieve':
            return OrganizationDetailSerializer
        return self.serializer_class

    def get_queryset(self):
        """Filtra organizaciones relacionadas con el usuario autenticado"""
        user = self.request.user
        membership_orgs = user.organizations.all()
        admin_orgs = user.administered_organizations.all()
        # Combinar los QuerySets y eliminar duplicados
        return (membership_orgs | admin_orgs).distinct()

    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        """Lista los miembros de una organización"""
        organization = self.get_object()
        memberships = OrganizationMembership.objects.filter(
            organization=organization)
        serializer = OrganizationMembershipSerializer(memberships, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def add_member(self, request, pk=None):
        """Añade un miembro directamente a la organización (sin invitación)"""
        organization = self.get_object()

        # Verificar si el usuario actual es administrador
        try:
            membership = OrganizationMembership.objects.get(
                organization=organization,
                user=request.user
            )
            if membership.role != 'ADMIN':
                return Response(
                    {"error": "No tienes permisos para añadir miembros a esta organización"},
                    status=status.HTTP_403_FORBIDDEN
                )
        except OrganizationMembership.DoesNotExist:
            return Response(
                {"error": "No tienes permisos para gestionar esta organización"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Obtener usuario a añadir
        user_id = request.data.get('user_id')
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {"error": "Usuario no encontrado"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Añadir miembro
        success, message = organization.add_member(
            user, role=request.data.get('role', 'MEMBER'))

        if success:
            return Response(
                {"message": message},
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {"error": message},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        """Elimina un miembro de la organización"""
        organization = self.get_object()

        # Verificar si el usuario actual es administrador
        try:
            membership = OrganizationMembership.objects.get(
                organization=organization,
                user=request.user
            )
            if membership.role != 'ADMIN':
                return Response(
                    {"error": "No tienes permisos para eliminar miembros de esta organización"},
                    status=status.HTTP_403_FORBIDDEN
                )
        except OrganizationMembership.DoesNotExist:
            return Response(
                {"error": "No tienes permisos para gestionar esta organización"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Obtener usuario a eliminar
        user_id = request.data.get('user_id')
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {"error": "Usuario no encontrado"},
                status=status.HTTP_404_NOT_FOUND
            )

        # No permitir eliminar al administrador principal
        if organization.admin_user == user:
            return Response(
                {"error": "No puedes eliminar al administrador principal de la organización"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Eliminar miembro
        success, message = organization.remove_member(user)

        if success:
            return Response(
                {"message": message},
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {"error": message},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def invite_member(self, request, pk=None):
        """Envía una invitación a un usuario para unirse a la organización"""
        organization = self.get_object()

        # Verificar si el usuario actual es administrador
        try:
            membership = OrganizationMembership.objects.get(
                organization=organization,
                user=request.user
            )
            if membership.role != 'ADMIN':
                return Response(
                    {"error": "No tienes permisos para invitar miembros a esta organización"},
                    status=status.HTTP_403_FORBIDDEN
                )
        except OrganizationMembership.DoesNotExist:
            return Response(
                {"error": "No tienes permisos para gestionar esta organización"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Verificar si la organización puede añadir más miembros
        if not organization.can_add_members:
            return Response(
                {"error": "La organización ha alcanzado su límite de miembros"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Crear la invitación
        email = request.data.get('email')
        message = request.data.get('message', '')

        # Generar token único
        token = str(uuid.uuid4())

        # Fecha de expiración (7 días)
        expires_at = timezone.now() + datetime.timedelta(days=7)

        invitation = OrganizationInvitation.objects.create(
            organization=organization,
            email=email,
            invited_by=request.user,
            token=token,
            message=message,
            expires_at=expires_at
        )

        # Aquí se podría implementar el envío de un correo electrónico
        # con el enlace que contiene el token

        serializer = OrganizationInvitationSerializer(invitation)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class OrganizationInvitationViewSet(viewsets.ModelViewSet):
    queryset = OrganizationInvitation.objects.all()
    serializer_class = OrganizationInvitationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filtra invitaciones relacionadas con el usuario autenticado"""
        return OrganizationInvitation.objects.filter(invited_by=self.request.user)

    @action(detail=False, methods=['get'])
    def pending(self, request):
        """Lista las invitaciones pendientes creadas por el usuario"""
        invitations = OrganizationInvitation.objects.filter(
            invited_by=request.user,
            status='PENDING'
        )
        serializer = self.get_serializer(invitations, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancela una invitación pendiente"""
        invitation = self.get_object()

        if invitation.status != 'PENDING':
            return Response(
                {"error": f"La invitación ya ha sido {invitation.get_status_display().lower()}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        invitation.status = 'REJECTED'
        invitation.save()

        return Response(
            {"message": "Invitación cancelada correctamente"},
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=['get'])
    def validate_token(self, request):
        """Valida un token de invitación"""
        token = request.query_params.get('token')

        if not token:
            return Response(
                {"error": "Token no proporcionado"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            invitation = OrganizationInvitation.objects.get(token=token)
        except OrganizationInvitation.DoesNotExist:
            return Response(
                {"error": "Invitación no encontrada"},
                status=status.HTTP_404_NOT_FOUND
            )

        if invitation.status != 'PENDING':
            return Response(
                {"error": f"La invitación ya ha sido {invitation.get_status_display().lower()}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if invitation.is_expired:
            invitation.status = 'EXPIRED'
            invitation.save()
            return Response(
                {"error": "La invitación ha expirado"},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(invitation)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def accept(self, request):
        """Acepta una invitación utilizando el token"""
        token = request.data.get('token')

        if not token:
            return Response(
                {"error": "Token no proporcionado"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            invitation = OrganizationInvitation.objects.get(token=token)
        except OrganizationInvitation.DoesNotExist:
            return Response(
                {"error": "Invitación no encontrada"},
                status=status.HTTP_404_NOT_FOUND
            )

        success, message = invitation.accept(request.user)

        if success:
            return Response(
                {"message": "Te has unido a la organización correctamente"},
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {"error": message},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['post'])
    def reject(self, request):
        """Rechaza una invitación utilizando el token"""
        token = request.data.get('token')

        if not token:
            return Response(
                {"error": "Token no proporcionado"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            invitation = OrganizationInvitation.objects.get(token=token)
        except OrganizationInvitation.DoesNotExist:
            return Response(
                {"error": "Invitación no encontrada"},
                status=status.HTTP_404_NOT_FOUND
            )

        success, message = invitation.reject()

        if success:
            return Response(
                {"message": "Invitación rechazada correctamente"},
                status=status.HTTP_200_OK
            )
        else:
            return Response(
                {"error": message},
                status=status.HTTP_400_BAD_REQUEST
            )

# Nuevas vistas para autenticación con Telegram


@api_view(['POST'])
@permission_classes([AllowAny])
def telegram_auth_widget(request):
    """
    Autenticación mediante widget oficial de Telegram
    """
    data = request.data

    # Verificar que los datos necesarios estén presentes
    required_fields = ['id', 'first_name', 'hash', 'auth_date']
    if not all(field in data for field in required_fields):
        return Response(
            {"error": "Datos de autenticación incompletos"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Verificar la firma de Telegram
    check_hash = data.pop('hash')

    # Crear una cadena de verificación
    data_check_arr = []
    for key, value in data.items():
        data_check_arr.append(f"{key}={value}")
    data_check_string = '\n'.join(sorted(data_check_arr))

    # Crear una clave secreta a partir del token del bot
    bot_token = settings.TELEGRAM_BOT_TOKEN
    secret_key = hashlib.sha256(bot_token.encode()).digest()

    # Calcular un hash HMAC-SHA256 usando la clave secreta
    computed_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        digestmod=hashlib.sha256
    ).hexdigest()

    # Verificar si el hash recibido coincide con el calculado
    if computed_hash != check_hash:
        return Response(
            {"error": "Firma de autenticación inválida"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Verificar si auth_date es reciente (menos de 1 día)
    auth_date = int(data.get('auth_date', 0))
    current_time = int(time.time())
    if current_time - auth_date > 86400:  # 86400 segundos = 1 día
        return Response(
            {"error": "Datos de autenticación expirados"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Autenticar o crear el usuario
    telegram_id = str(data.get('id'))

    # Buscar usuario existente por su telegram_id como external_id
    try:
        user = User.objects.get(external_id=telegram_id, platform='telegram')
        # Actualizar datos del usuario
        user.first_name = data.get('first_name', '')
        user.username = data.get('username', '')
        user.save()
    except User.DoesNotExist:
        # Crear nuevo usuario
        user = User.objects.create(
            external_id=telegram_id,
            platform='telegram',
            first_name=data.get('first_name', ''),
            username=data.get('username', '')
        )

    # Generar token JWT
    token = RefreshToken.for_user(user)

    # Devolver respuesta
    return Response({
        'refresh': str(token),
        'access': str(token.access_token),
        'user': UserSerializer(user).data
    })


@api_view(['POST'])
@permission_classes([AllowAny])
def request_verification_code(request):
    """
    Solicita un código de verificación para un número de teléfono
    El código se enviará al usuario a través del bot de Telegram
    """
    phone_number = request.data.get('phone_number')
    if not phone_number:
        return Response(
            {"error": "Se requiere un número de teléfono"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Verificar si el número ya tiene una cuenta registrada
    numero_registrado = User.objects.filter(phone_number=phone_number).exists()

    # Generar código de verificación
    verification = TelegramVerification.generate_code(phone_number)

    # Intentar encontrar el chat_id de Telegram asociado con este número
    # Esto requiere que el usuario ya haya interactuado con el bot
    from users.models import Chat
    chat = None

    try:
        # Buscar usuario por número de teléfono
        user = User.objects.get(phone_number=phone_number)
        # Buscar chat de Telegram asociado
        chat = Chat.objects.filter(platform='TELEGRAM', user=user).first()

        if chat:
            # Guardar chat_id en la verificación
            verification.telegram_chat_id = chat.platform_chat_id
            verification.save()
    except (User.DoesNotExist, Chat.DoesNotExist):
        pass

    # Si tenemos un chat_id, enviar el código a través del bot de Telegram
    if chat:
        try:
            bot_token = settings.TELEGRAM_BOT_TOKEN
            message = f"Tu código de verificación para Tresqu es: *{verification.verification_code}*\n\nEste código expirará en 5 minutos."

            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                "chat_id": chat.platform_chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }

            response = requests.post(url, data=data)
            response.raise_for_status()

            return Response({
                "message": "Código de verificación enviado a Telegram",
                "success": True,
                "has_telegram": True,
                "numero_registrado": numero_registrado
            })
        except Exception as e:
            # Si falla el envío por Telegram, devolver mensaje para UI alternativa
            return Response({
                "message": f"No se pudo enviar el código por Telegram: {str(e)}",
                "success": False,
                "has_telegram": True,
                "numero_registrado": numero_registrado
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    else:
        # Si no hay chat asociado, indicar que se debe usar otro método
        return Response({
            "message": "No se encontró una cuenta de Telegram asociada a este número",
            "success": True,
            "has_telegram": False,
            "numero_registrado": numero_registrado
        })


@api_view(['POST'])
@permission_classes([AllowAny])
def verify_code(request):
    """
    Verifica el código enviado por el usuario y genera un token JWT
    """
    phone_number = request.data.get('phone_number')
    code = request.data.get('code')

    if not phone_number or not code:
        return Response(
            {"error": "Se requiere número de teléfono y código"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Verificar si ya existe una cuenta con este número
    numero_registrado = User.objects.filter(phone_number=phone_number).exists()

    # Buscar la verificación más reciente para este número
    try:
        verification = TelegramVerification.objects.filter(
            phone_number=phone_number,
            verification_code=code,
            is_verified=False
        ).latest('created_at')

        # Verificar si el código ha expirado
        if verification.is_expired():
            return Response(
                {"error": "El código ha expirado"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Marcar como verificado
        verification.is_verified = True
        verification.save()

        # Buscar o crear usuario
        try:
            user = User.objects.get(phone_number=phone_number)
            user_action = "login"  # Indica que el usuario ha iniciado sesión
        except User.DoesNotExist:
            # Crear usuario nuevo con este número de teléfono
            user = User.objects.create(
                external_id=f"phone_{phone_number}",
                platform='phone',
                phone_number=phone_number
            )
            user_action = "register"  # Indica que se ha registrado un nuevo usuario

        # Generar token JWT
        token = RefreshToken.for_user(user)

        # Devolver respuesta
        return Response({
            'refresh': str(token),
            'access': str(token.access_token),
            'user': UserSerializer(user).data,
            'user_action': user_action,
            'message': "Inicio de sesión exitoso" if user_action == "login" else "Registro exitoso"
        })

    except TelegramVerification.DoesNotExist:
        return Response(
            {"error": "Código inválido"},
            status=status.HTTP_400_BAD_REQUEST
        )


class TrackingLinkViewSet(viewsets.ModelViewSet):
    """
    ViewSet para gestionar enlaces de seguimiento de empresas
    """
    queryset = TrackingLink.objects.all()
    # Solo usuarios autenticados pueden gestionar enlaces
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        """Usar diferentes serializers según la acción"""
        if self.action == 'create':
            return TrackingLinkCreateSerializer
        elif self.action == 'stats':
            return TrackingLinkStatsSerializer
        return TrackingLinkSerializer

    def get_queryset(self):
        """Filtrar enlaces activos por defecto"""
        queryset = TrackingLink.objects.all()

        # Filtros opcionales
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')

        # Filtrar por código
        code = self.request.query_params.get('code', None)
        if code:
            queryset = queryset.filter(code__icontains=code)

        # Filtrar por nombre
        name = self.request.query_params.get('name', None)
        if name:
            queryset = queryset.filter(name__icontains=name)

        return queryset.order_by('-created_at')

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """
        Obtiene estadísticas detalladas de un enlace específico
        """
        tracking_link = self.get_object()
        serializer = TrackingLinkStatsSerializer(tracking_link)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def toggle_active(self, request, pk=None):
        """
        Activa o desactiva un enlace de seguimiento
        """
        tracking_link = self.get_object()
        tracking_link.is_active = not tracking_link.is_active
        tracking_link.save()

        serializer = TrackingLinkSerializer(tracking_link)
        return Response({
            'message': f'Enlace {"activado" if tracking_link.is_active else "desactivado"} correctamente',
            'tracking_link': serializer.data
        })

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        Obtiene un resumen general de todos los enlaces
        """
        total_links = TrackingLink.objects.count()
        active_links = TrackingLink.objects.filter(is_active=True).count()
        total_registrations = sum(
            link.total_registrations for link in TrackingLink.objects.all())

        # Top 5 enlaces con más registros
        top_links = TrackingLink.objects.order_by('-total_registrations')[:5]
        top_links_data = TrackingLinkSerializer(top_links, many=True).data

        return Response({
            'summary': {
                'total_links': total_links,
                'active_links': active_links,
                'inactive_links': total_links - active_links,
                'total_registrations': total_registrations
            },
            'top_performing_links': top_links_data
        })

    @action(detail=True, methods=['get'])
    def generate_links(self, request, pk=None):
        """
        Genera enlaces listos para usar en diferentes plataformas
        """
        tracking_link = self.get_object()

        # Obtener número del bot de WhatsApp desde configuración o usar uno por defecto
        bot_whatsapp_number = getattr(
            settings, 'WHATSAPP_BOT_NUMBER', '573001234567')

        links = {
            'whatsapp': tracking_link.get_whatsapp_link(bot_whatsapp_number),
            # Actualizar con tu bot de Telegram
            'telegram': f"https://t.me/tu_bot?start={tracking_link.code}",
            'direct_code': tracking_link.code
        }

        return Response({
            'tracking_link': TrackingLinkSerializer(tracking_link).data,
            'generated_links': links,
            'instructions': {
                'whatsapp': 'Comparte este enlace para que los usuarios lleguen directamente a WhatsApp con el código',
                'telegram': 'Comparte este enlace para que los usuarios lleguen directamente a Telegram con el código',
                'direct_code': 'Los usuarios pueden usar este código manualmente durante el registro'
            }
        })
