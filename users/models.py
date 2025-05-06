from django.db import models
from pgvector.django import VectorField
from django.utils import timezone
import datetime
import random
import string


class TelegramVerification(models.Model):
    phone_number = models.CharField(max_length=20)
    verification_code = models.CharField(max_length=6)
    telegram_chat_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)
    used_for_registration = models.BooleanField(default=False)

    @classmethod
    def generate_code(cls, phone_number):
        """Genera un código de verificación de 6 dígitos para el número de teléfono dado"""
        code = ''.join(random.choices(string.digits, k=6))
        verification = cls(phone_number=phone_number, verification_code=code)
        verification.save()
        return verification

    def is_expired(self):
        """Verifica si el código ha expirado (5 minutos)"""
        return timezone.now() > self.created_at + datetime.timedelta(minutes=5)


class SubscriptionPlan(models.Model):
    PLAN_CHOICES = [
        ('BASIC', 'Básico'),
        ('PREMIUM', 'Premium'),
        ('BUSINESS', 'Empresas'),
    ]

    name = models.CharField(max_length=50, choices=PLAN_CHOICES)
    description = models.TextField()
    price_monthly = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)
    price_yearly = models.DecimalField(
        max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Características de los planes
    allows_income_expense_tracking = models.BooleanField(default=True)
    allows_basic_statistics = models.BooleanField(default=True)
    allows_debt_savings_tracking = models.BooleanField(default=False)
    allows_detailed_statistics = models.BooleanField(default=False)
    allows_reports = models.BooleanField(default=False)
    unlimited_records = models.BooleanField(default=False)
    allows_debt_planning = models.BooleanField(default=False)
    allows_savings_goals = models.BooleanField(default=False)
    allows_export = models.BooleanField(default=False)
    allows_voice_interaction = models.BooleanField(default=False)
    priority_support = models.BooleanField(default=False)
    allows_multi_user = models.BooleanField(default=False)
    allows_custom_reports = models.BooleanField(default=False)

    def __str__(self):
        return self.get_name_display()

    @classmethod
    def get_basic_plan(cls):
        plan, _ = cls.objects.get_or_create(
            name='BASIC',
            defaults={
                'description': 'Plan Básico gratuito',
                'price_monthly': 0,
                'price_yearly': 0,
                'allows_income_expense_tracking': True,
                'allows_basic_statistics': True,
            }
        )
        return plan

    @classmethod
    def get_premium_plan(cls):
        plan, _ = cls.objects.get_or_create(
            name='PREMIUM',
            defaults={
                'description': 'Plan Premium para usuarios avanzados',
                'price_monthly': 5,
                'price_yearly': 50,
                'allows_income_expense_tracking': True,
                'allows_basic_statistics': True,
                'allows_debt_savings_tracking': True,
                'allows_detailed_statistics': True,
                'allows_reports': True,
                'unlimited_records': True,
                'allows_debt_planning': True,
                'allows_savings_goals': True,
                'allows_export': True,
                'allows_voice_interaction': True,
                'priority_support': True,
            }
        )
        return plan

    @classmethod
    def get_business_plan(cls):
        plan, _ = cls.objects.get_or_create(
            name='BUSINESS',
            defaults={
                'description': 'Plan Empresas para equipos y empresas',
                'price_monthly': 20,
                'price_yearly': 200,
                'allows_income_expense_tracking': True,
                'allows_basic_statistics': True,
                'allows_debt_savings_tracking': True,
                'allows_detailed_statistics': True,
                'allows_reports': True,
                'unlimited_records': True,
                'allows_debt_planning': True,
                'allows_savings_goals': True,
                'allows_export': True,
                'allows_voice_interaction': True,
                'priority_support': True,
                'allows_multi_user': True,
                'allows_custom_reports': True,
            }
        )
        return plan


class User(models.Model):
    external_id = models.CharField(max_length=100, unique=True)
    platform = models.CharField(max_length=50)
    first_name = models.CharField(max_length=100, blank=True)
    username = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    default_currency = models.CharField(
        max_length=3, default='USD', help_text="Código ISO 4217 de la moneda por defecto")
    # Campo para almacenar embeddings (opcional)
    embedding = VectorField(dimensions=1536, null=True)

    # Plan de suscripción
    subscription_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        default=None,
        related_name='users'
    )
    subscription_active = models.BooleanField(default=True)
    subscription_start_date = models.DateTimeField(null=True, blank=True)
    subscription_end_date = models.DateTimeField(null=True, blank=True)
    is_yearly_billing = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.first_name or self.username or self.external_id}"

    @property
    def is_premium(self):
        return self.subscription_plan and self.subscription_plan.name in ['PREMIUM', 'BUSINESS'] and self.subscription_active

    @property
    def is_business(self):
        return self.subscription_plan and self.subscription_plan.name == 'BUSINESS' and self.subscription_active

    @property
    def is_trial_expired(self):
        """Verifica si el período de prueba ha expirado"""
        if not self.subscription_end_date:
            return False
        return timezone.now() >= self.subscription_end_date

    def assign_premium_trial(self):
        """Asigna el plan premium de prueba por un mes al usuario"""
        premium_plan = SubscriptionPlan.get_premium_plan()
        start_date = timezone.now()
        end_date = start_date + datetime.timedelta(days=30)

        # Actualizar el usuario
        self.subscription_plan = premium_plan
        self.subscription_active = True
        self.subscription_start_date = start_date
        self.subscription_end_date = end_date
        self.save()

        # Registrar en el historial de suscripciones
        Subscription.objects.create(
            user=self,
            plan=premium_plan,
            start_date=start_date,
            end_date=end_date,
            is_active=True,
            amount_paid=0,  # Gratis durante el periodo de prueba
            is_yearly_billing=False,
        )

        return True

    def downgrade_to_basic(self):
        """Cambia al usuario al plan básico"""
        basic_plan = SubscriptionPlan.get_basic_plan()
        start_date = timezone.now()

        # Desactivar suscripciones anteriores
        Subscription.objects.filter(user=self, is_active=True).update(
            is_active=False,
            end_date=start_date
        )

        # Actualizar el usuario
        self.subscription_plan = basic_plan
        self.subscription_active = True
        self.subscription_start_date = start_date
        self.subscription_end_date = None  # El plan básico no expira
        self.save()

        # Registrar en el historial
        Subscription.objects.create(
            user=self,
            plan=basic_plan,
            start_date=start_date,
            end_date=None,
            is_active=True,
            amount_paid=0,
            is_yearly_billing=False,
        )

        return True


class Subscription(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='subscription_history')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    payment_reference = models.CharField(max_length=255, blank=True, null=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    is_yearly_billing = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} - {self.plan.get_name_display()} ({self.start_date.strftime('%Y-%m-%d')})"


class Organization(models.Model):
    name = models.CharField(max_length=200)
    admin_user = models.ForeignKey(
        User, related_name='administered_organizations', on_delete=models.PROTECT)
    members = models.ManyToManyField(
        User, through='OrganizationMembership', related_name='organizations')
    subscription_active = models.BooleanField(default=True)
    subscription_start_date = models.DateTimeField(auto_now_add=True)
    subscription_end_date = models.DateTimeField(null=True, blank=True)
    max_members = models.IntegerField(default=5)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def current_member_count(self):
        return self.members.count()

    @property
    def can_add_members(self):
        return self.current_member_count < self.max_members

    def add_member(self, user, role='MEMBER'):
        """Añade un miembro a la organización si hay espacio disponible"""
        if not self.can_add_members:
            return False, "Se ha alcanzado el límite de miembros para esta organización"

        # Verificar si el usuario ya es miembro
        if OrganizationMembership.objects.filter(organization=self, user=user).exists():
            return False, "El usuario ya es miembro de esta organización"

        # Añadir el usuario
        OrganizationMembership.objects.create(
            organization=self,
            user=user,
            role=role
        )

        # Actualizar el plan del usuario
        business_plan = SubscriptionPlan.get_business_plan()
        user.subscription_plan = business_plan
        user.subscription_active = True
        user.save()

        return True, "Usuario añadido correctamente a la organización"

    def remove_member(self, user):
        """Elimina un miembro de la organización"""
        try:
            membership = OrganizationMembership.objects.get(
                organization=self, user=user)
            if membership.role == 'ADMIN' and self.admin_user == user:
                return False, "No puedes eliminar al administrador principal de la organización"

            membership.delete()

            # Revertir al usuario a plan básico
            user.downgrade_to_basic()

            return True, "Usuario eliminado correctamente de la organización"
        except OrganizationMembership.DoesNotExist:
            return False, "El usuario no es miembro de esta organización"

    @classmethod
    def create_organization(cls, name, admin_user, initial_members=None, max_members=5):
        """
        Crea una nueva organización con un plan de empresa

        Args:
            name: Nombre de la organización
            admin_user: Usuario administrador
            initial_members: Lista opcional de usuarios iniciales
            max_members: Número máximo de miembros permitidos
        """
        # Establecer el plan Business para el administrador
        business_plan = SubscriptionPlan.get_business_plan()
        admin_user.subscription_plan = business_plan
        admin_user.subscription_active = True
        admin_user.save()

        # Crear la organización
        organization = cls.objects.create(
            name=name,
            admin_user=admin_user,
            max_members=max_members,
            subscription_end_date=timezone.now() + datetime.timedelta(days=365)  # 1 año
        )

        # Añadir al administrador como miembro
        OrganizationMembership.objects.create(
            organization=organization,
            user=admin_user,
            role='ADMIN'
        )

        # Añadir miembros iniciales si se proporcionan
        if initial_members:
            for user in initial_members:
                if user != admin_user and organization.current_member_count < organization.max_members:
                    organization.add_member(user)

        return organization


class OrganizationMembership(models.Model):
    ROLE_CHOICES = [
        ('ADMIN', 'Administrador'),
        ('MEMBER', 'Miembro'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=50, choices=ROLE_CHOICES, default='MEMBER')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'organization')

    def __str__(self):
        return f"{self.user} en {self.organization} ({self.get_role_display()})"


class OrganizationInvitation(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pendiente'),
        ('ACCEPTED', 'Aceptada'),
        ('REJECTED', 'Rechazada'),
        ('EXPIRED', 'Expirada'),
    ]

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    invited_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='sent_invitations')
    token = models.CharField(max_length=100, unique=True)
    message = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='PENDING')
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Invitación para {self.email} a {self.organization}"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at

    def accept(self, user):
        """El usuario acepta la invitación"""
        if self.status != 'PENDING':
            return False, f"La invitación ya ha sido {self.get_status_display().lower()}"

        if self.is_expired:
            self.status = 'EXPIRED'
            self.save()
            return False, "La invitación ha expirado"

        # Verificar si la organización puede añadir más miembros
        if not self.organization.can_add_members:
            self.status = 'REJECTED'
            self.save()
            return False, "La organización ha alcanzado su límite de miembros"

        # Añadir el usuario a la organización
        success, message = self.organization.add_member(user)

        if success:
            self.status = 'ACCEPTED'
            self.save()

        return success, message

    def reject(self):
        """Rechazar la invitación"""
        if self.status != 'PENDING':
            return False, f"La invitación ya ha sido {self.get_status_display().lower()}"

        self.status = 'REJECTED'
        self.save()
        return True, "Invitación rechazada"
