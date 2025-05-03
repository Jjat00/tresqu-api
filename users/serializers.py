from rest_framework import serializers
from .models import User, SubscriptionPlan, Subscription, Organization, OrganizationMembership, OrganizationInvitation


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
