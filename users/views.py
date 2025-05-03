from django.shortcuts import render
from django.utils import timezone
import uuid
import datetime

# Create your views here.

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import User, SubscriptionPlan, Subscription, Organization, OrganizationMembership, OrganizationInvitation
from .serializers import (
    UserSerializer,
    SubscriptionPlanSerializer,
    SubscriptionSerializer,
    OrganizationSerializer,
    OrganizationDetailSerializer,
    OrganizationCreateSerializer,
    OrganizationMembershipSerializer,
    OrganizationInvitationSerializer
)


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

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


class SubscriptionViewSet(viewsets.ModelViewSet):
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer


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
