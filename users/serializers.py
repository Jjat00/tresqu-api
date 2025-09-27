from rest_framework import serializers
from .models import User, SubscriptionPlan, Subscription, Organization, OrganizationMembership, OrganizationInvitation, TrackingLink
from cashbotapp.settings import WHATSAPP_BOT_NUMBER


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = '__all__'


class SubscriptionSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(
        source='plan.get_name_display', read_only=True)

    class Meta:
        model = Subscription
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class UserSerializer(serializers.ModelSerializer):
    subscription_plan_details = SubscriptionPlanSerializer(
        source='subscription_plan', read_only=True)

    class Meta:
        model = User
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    user_details = UserSerializer(source='user', read_only=True)
    role_display = serializers.CharField(
        source='get_role_display', read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = ['id', 'user', 'user_details', 'organization',
                  'role', 'role_display', 'joined_at']
        read_only_fields = ('joined_at',)


class OrganizationSerializer(serializers.ModelSerializer):
    admin_user_details = UserSerializer(source='admin_user', read_only=True)
    current_member_count = serializers.IntegerField(read_only=True)
    can_add_members = serializers.BooleanField(read_only=True)

    class Meta:
        model = Organization
        fields = [
            'id', 'name', 'admin_user', 'admin_user_details',
            'subscription_active', 'subscription_start_date', 'subscription_end_date',
            'max_members', 'current_member_count', 'can_add_members',
            'created_at', 'updated_at'
        ]
        read_only_fields = ('created_at', 'updated_at',
                            'subscription_start_date')


class OrganizationDetailSerializer(OrganizationSerializer):
    members = OrganizationMembershipSerializer(
        source='organizationmembership_set', many=True, read_only=True)

    class Meta(OrganizationSerializer.Meta):
        fields = OrganizationSerializer.Meta.fields + ['members']


class OrganizationInvitationSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(
        source='organization.name', read_only=True)
    invited_by_name = serializers.CharField(
        source='invited_by.__str__', read_only=True)
    status_display = serializers.CharField(
        source='get_status_display', read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = OrganizationInvitation
        fields = [
            'id', 'organization', 'organization_name', 'email',
            'invited_by', 'invited_by_name', 'message', 'status',
            'status_display', 'expires_at', 'is_expired',
            'created_at', 'updated_at'
        ]
        read_only_fields = ('token', 'status', 'created_at', 'updated_at')


class OrganizationCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ['name', 'max_members']

    def create(self, validated_data):
        # El usuario que creó es el administrador
        user = self.context['request'].user
        return Organization.create_organization(
            name=validated_data['name'],
            admin_user=user,
            max_members=validated_data.get('max_members', 5)
        )


class TrackingLinkSerializer(serializers.ModelSerializer):
    """Serializer para enlaces de seguimiento"""
    conversion_rate = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    whatsapp_link = serializers.SerializerMethodField()

    class Meta:
        model = TrackingLink
        fields = [
            'id', 'code', 'name', 'description', 'is_active', 'expires_at',
            'total_clicks', 'total_registrations', 'conversion_rate',
            'is_expired', 'whatsapp_link', 'created_at', 'updated_at'
        ]
        read_only_fields = (
            'total_clicks', 'total_registrations', 'created_at', 'updated_at')

    def get_whatsapp_link(self, obj):
        """Genera el enlace de WhatsApp del bot"""
        # Número del bot de IA - en producción obtenerlo de settings
        bot_phone_number = WHATSAPP_BOT_NUMBER
        return obj.get_whatsapp_link(bot_phone_number)


class TrackingLinkCreateSerializer(serializers.ModelSerializer):
    """Serializer para crear enlaces de seguimiento"""

    class Meta:
        model = TrackingLink
        fields = ['name', 'description', 'code', 'is_active', 'expires_at']

    def validate_code(self, value):
        """Valida que el código sea único si se proporciona"""
        if value:
            # Limpiar el código
            clean_code = value.lower().replace(' ', '_').replace('-', '_')
            clean_code = ''.join(
                c for c in clean_code if c.isalnum() or c == '_')

            if TrackingLink.objects.filter(code=clean_code).exists():
                raise serializers.ValidationError("Este código ya existe")

            return clean_code
        return value

    def create(self, validated_data):
        """Crea un enlace de seguimiento"""
        if not validated_data.get('code'):
            # Generar código automáticamente
            validated_data['code'] = TrackingLink.generate_code(
                validated_data['name'])

        return super().create(validated_data)


class TrackingLinkStatsSerializer(serializers.ModelSerializer):
    """Serializer para estadísticas detalladas de enlaces"""
    conversion_rate = serializers.ReadOnlyField()
    is_expired = serializers.ReadOnlyField()
    recent_registrations = serializers.SerializerMethodField()

    class Meta:
        model = TrackingLink
        fields = [
            'id', 'code', 'name', 'description', 'is_active', 'expires_at',
            'total_clicks', 'total_registrations', 'conversion_rate',
            'is_expired', 'recent_registrations', 'created_at', 'updated_at'
        ]

    def get_recent_registrations(self, obj):
        """Obtiene los últimos 10 usuarios registrados desde este enlace"""
        recent_users = obj.users.order_by('-created_at')[:10]
        return [{
            'id': user.id,
            'name': user.first_name or user.username or 'Usuario',
            'platform': user.platform,
            'registered_at': user.created_at
        } for user in recent_users]
