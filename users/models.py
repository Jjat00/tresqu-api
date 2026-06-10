from django.db import models
from pgvector.django import VectorField, CosineDistance
from django.utils import timezone
import datetime
import random
import string
import secrets
from cashbotapp.settings import WHATSAPP_BOT_NUMBER


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


class TrackingLink(models.Model):
    """
    Modelo para rastrear enlaces de referido de empresas/partners
    """
    code = models.CharField(
        max_length=50,
        unique=True,
        help_text="Código único del enlace (ej: empresa_abc_2024)"
    )
    name = models.CharField(
        max_length=200,
        help_text="Nombre descriptivo (ej: Campaña Empresa ABC)"
    )
    description = models.TextField(
        blank=True,
        help_text="Descripción opcional del enlace o campaña"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Si el enlace está activo para recibir nuevos usuarios"
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Fecha de expiración del enlace (opcional)"
    )

    # Campos para analytics
    total_clicks = models.PositiveIntegerField(
        default=0,
        help_text="Total de clics registrados (si se implementa tracking de clics)"
    )
    total_registrations = models.PositiveIntegerField(
        default=0,
        help_text="Total de usuarios registrados desde este enlace"
    )

    class Meta:
        verbose_name = "Enlace de Seguimiento"
        verbose_name_plural = "Enlaces de Seguimiento"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def is_expired(self):
        """Verifica si el enlace ha expirado"""
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    @property
    def conversion_rate(self):
        """Calcula la tasa de conversión (registros/clics)"""
        if self.total_clicks == 0:
            return 0
        return (self.total_registrations / self.total_clicks) * 100

    @classmethod
    def generate_code(cls, base_name=None):
        """Genera un código único para el enlace"""
        if base_name:
            # Limpiar el nombre base
            clean_name = base_name.lower().replace(' ', '_').replace('-', '_')
            # Limitar a caracteres alfanuméricos y guiones bajos
            clean_name = ''.join(
                c for c in clean_name if c.isalnum() or c == '_')
            # Agregar sufijo aleatorio para garantizar unicidad
            suffix = secrets.token_hex(4)
            code = f"{clean_name}_{suffix}"
        else:
            # Código completamente aleatorio
            code = f"ref_{secrets.token_hex(6)}"

        # Verificar que no exista
        while cls.objects.filter(code=code).exists():
            suffix = secrets.token_hex(4)
            if base_name:
                clean_name = base_name.lower().replace(' ', '_').replace('-', '_')
                clean_name = ''.join(
                    c for c in clean_name if c.isalnum() or c == '_')
                code = f"{clean_name}_{suffix}"
            else:
                code = f"ref_{secrets.token_hex(6)}"

        return code

    def increment_registrations(self):
        """Incrementa el contador de registros"""
        self.total_registrations += 1
        self.save(update_fields=['total_registrations'])

    def get_whatsapp_link(self, bot_phone_number):
        """
        Genera el enlace de WhatsApp del bot de IA con el código de referido embebido

        Args:
            bot_phone_number: Número de WhatsApp del bot de IA (siempre el mismo bot, pero configurable)

        Returns:
            str: URL de WhatsApp que dirige al bot con el código de referido como mensaje predefinido
        """
        # Normalizar número de teléfono del bot (remover espacios, guiones, paréntesis, etc.)
        clean_phone = ''.join(filter(str.isdigit, bot_phone_number))

        # Si el número está vacío, usar número por defecto de Colombia
        if not clean_phone:
            clean_phone = WHATSAPP_BOT_NUMBER  # Número de ejemplo del bot

        # Si el número no tiene código de país, agregar Colombia por defecto
        elif len(clean_phone) == 10:
            clean_phone = f"57{clean_phone}"
        # Si ya tiene código de país (11+ dígitos), usar tal como está

        # Mensaje predefinido con el código de la empresa
        message = f"Hola vengo de {self.code.upper()}, ¡Me gustaría llevar un mejor control de mis finanzas!"
        return f"https://wa.me/{clean_phone}?text={message.replace(' ', '%20')}"

    def get_telegram_link(self, bot_username="TresquBot"):
        """
        Genera el enlace de Telegram del bot de IA con el código de referido como parámetro

        Args:
            bot_username: Nombre de usuario del bot de Telegram (sin @)

        Returns:
            str: URL de Telegram que dirige al bot con el código de referido como parámetro de /start
        """
        # Limpiar el nombre de usuario del bot (remover @ si existe)
        clean_username = bot_username.lstrip('@')

        # Si está vacío, usar nombre por defecto
        if not clean_username:
            clean_username = "TresquBot"

        # Generar enlace con deep linking usando el código como parámetro
        return f"https://t.me/{clean_username}?start={self.code}"


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
    timezone = models.CharField(
        max_length=50, default='America/Bogota',
        help_text="Zona horaria del usuario (ej: America/Bogota, America/New_York)")
    # Campo para almacenar embeddings (opcional)
    embedding = VectorField(dimensions=1536, null=True)

    # Tracking de origen
    source_tracking_link = models.ForeignKey(
        TrackingLink,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        help_text="Enlace de seguimiento de origen del usuario"
    )

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

    # NOTA: Los límites ahora se manejan mensualmente a través del modelo MonthlyUsage
    # No necesitamos contadores totales, solo mensuales

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

    def get_current_monthly_usage(self):
        """Obtiene el uso mensual actual del usuario"""
        return MonthlyUsage.get_current_usage(self)

    def can_add_expense(self):
        """Verifica si el usuario puede agregar un gasto este mes"""
        # Asegurar que el usuario tenga un plan básico por defecto
        self.assign_basic_plan_if_none()

        monthly_usage = self.get_current_monthly_usage()
        return monthly_usage.can_add_expense()

    def can_add_income(self):
        """Verifica si el usuario puede agregar un ingreso este mes"""
        # Asegurar que el usuario tenga un plan básico por defecto
        self.assign_basic_plan_if_none()

        monthly_usage = self.get_current_monthly_usage()
        return monthly_usage.can_add_income()

    def get_usage_summary(self):
        """Obtiene un resumen del uso mensual actual"""
        monthly_usage = self.get_current_monthly_usage()
        return monthly_usage.get_usage_summary()

    def assign_basic_plan_if_none(self):
        """Asigna el plan básico si el usuario no tiene ningún plan"""
        if not self.subscription_plan:
            basic_plan = SubscriptionPlan.get_basic_plan()
            self.subscription_plan = basic_plan
            self.subscription_active = True
            self.subscription_start_date = timezone.now()
            self.subscription_end_date = None  # Plan básico no expira
            self.save()

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


# Nuevos modelos para manejar chats y mensajes de cualquier plataforma
class Chat(models.Model):
    PLATFORM_CHOICES = [
        ('TELEGRAM', 'Telegram'),
        ('WHATSAPP', 'WhatsApp'),
        # Agregar más plataformas aquí
    ]

    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES)
    platform_chat_id = models.CharField(max_length=100)
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='chats')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('platform', 'platform_chat_id')

    def __str__(self):
        return f"{self.platform} Chat: {self.platform_chat_id}"


class Message(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ('incoming', 'Incoming'),
        ('outgoing', 'Outgoing'),
    ]

    chat = models.ForeignKey(
        Chat, on_delete=models.CASCADE, related_name='messages')
    platform_message_id = models.CharField(max_length=100)
    message_type = models.CharField(
        max_length=20, choices=MESSAGE_TYPE_CHOICES)
    text = models.TextField()
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # No aplicamos restricción de BD para preservar datos duplicados existentes
    # La prevención se maneja a nivel de aplicación con cache y verificaciones

    @classmethod
    def find_similar(cls, user, embedding, limit=5, exclude_ids=None):
        """
        Encuentra mensajes del usuario similares al embedding proporcionado,
        en cualquier canal (Telegram/WhatsApp). Devuelve un queryset anotado
        con ``distance`` (coseno) y ordenado del más similar al menos.
        """
        if embedding is None:
            return cls.objects.none()

        qs = cls.objects.filter(
            chat__user=user,
            embedding__isnull=False,
        )
        if exclude_ids:
            qs = qs.exclude(id__in=exclude_ids)
        return qs.annotate(
            distance=CosineDistance('embedding', embedding)
        ).order_by('distance')[:limit]

    def __str__(self):
        return f"{self.get_message_type_display()} message: {self.text[:50]}"


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


# ========================================
# MODELO PARA USO MENSUAL
# ========================================

class MonthlyUsage(models.Model):
    """
    Modelo para rastrear el uso mensual de cada usuario
    Se reinicia automáticamente cada mes
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='monthly_usage'
    )
    year = models.PositiveIntegerField(
        help_text="Año del período de uso"
    )
    month = models.PositiveIntegerField(
        help_text="Mes del período de uso (1-12)"
    )

    # Contadores mensuales
    expenses_count = models.PositiveIntegerField(
        default=0,
        help_text="Número de gastos registrados este mes"
    )
    incomes_count = models.PositiveIntegerField(
        default=0,
        help_text="Número de ingresos registrados este mes"
    )

    # Metadatos
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'year', 'month')
        verbose_name = "Uso Mensual"
        verbose_name_plural = "Uso Mensual"
        indexes = [
            models.Index(fields=['user', 'year', 'month']),
            models.Index(fields=['year', 'month']),
        ]

    def __str__(self):
        return f"{self.user} - {self.year}/{self.month:02d} (G:{self.expenses_count}, I:{self.incomes_count})"

    @classmethod
    def get_or_create_current(cls, user):
        """
        Obtiene o crea el registro de uso para el período actual del usuario

        REGLAS:
        - Plan BASIC: Siempre mes calendario (1-31)
        - Plan PREMIUM/BUSINESS: Período de aniversario desde el cambio de plan
        """
        period_start, period_end = cls.calculate_user_billing_period(user)

        # Crear clave única basada en el período
        period_key = f"{period_start.strftime('%Y%m%d')}_{period_end.strftime('%Y%m%d')}"

        # Buscar registro existente para este período
        existing_usage = cls.objects.filter(
            user=user,
            year=period_start.year,
            month=period_start.month
        ).first()

        if existing_usage:
            # Verificar si el período coincide
            existing_start, existing_end = cls.calculate_period_from_record(
                existing_usage)
            if existing_start == period_start and existing_end == period_end:
                return existing_usage, False

        # Crear nuevo registro para el período actual
        usage, created = cls.objects.get_or_create(
            user=user,
            year=period_start.year,
            month=period_start.month,
            defaults={
                'expenses_count': 0,
                'incomes_count': 0
            }
        )
        return usage, created

    @classmethod
    def calculate_user_billing_period(cls, user, reference_date=None):
        """
        Calcula el período de facturación para un usuario específico
        """
        if reference_date is None:
            reference_date = timezone.now().date()

        # REGLA 1: Plan BASIC siempre usa mes calendario
        if not user.subscription_plan or user.subscription_plan.name == 'BASIC':
            return cls.calculate_calendar_month_period(reference_date)

        # REGLA 2: Plan PREMIUM/BUSINESS usa aniversario mensual
        if user.subscription_plan.name in ['PREMIUM', 'BUSINESS']:
            return cls.calculate_anniversary_period(user, reference_date)

        # Fallback: mes calendario
        return cls.calculate_calendar_month_period(reference_date)

    @classmethod
    def calculate_calendar_month_period(cls, reference_date):
        """
        Calcula período de mes calendario (1 al último día del mes)
        """
        from datetime import datetime, timedelta

        year = reference_date.year
        month = reference_date.month

        period_start = datetime(year, month, 1).date()

        # Calcular último día del mes
        if month == 12:
            next_month_first = datetime(year + 1, 1, 1).date()
        else:
            next_month_first = datetime(year, month + 1, 1).date()

        period_end = next_month_first - timedelta(days=1)

        return period_start, period_end

    @classmethod
    def calculate_anniversary_period(cls, user, reference_date):
        """
        Calcula período de aniversario mensual desde el cambio de plan
        """
        from datetime import datetime, timedelta
        from dateutil.relativedelta import relativedelta

        subscription_start = user.subscription_start_date
        if not subscription_start:
            # Fallback: usar mes calendario si no hay fecha de suscripción
            return cls.calculate_calendar_month_period(reference_date)

        # Convertir a date si es datetime
        if hasattr(subscription_start, 'date'):
            subscription_start = subscription_start.date()

        # Día del mes en que inicia el período de facturación
        billing_day = subscription_start.day

        # Calcular el período actual
        current_year = reference_date.year
        current_month = reference_date.month

        # Intentar crear la fecha de inicio del período actual
        try:
            period_start = datetime(
                current_year, current_month, billing_day).date()
        except ValueError:
            # Si el día no existe en el mes actual (ej: 31 en febrero)
            # Usar el último día del mes
            if current_month == 12:
                next_month = datetime(current_year + 1, 1, 1).date()
            else:
                next_month = datetime(
                    current_year, current_month + 1, 1).date()
            last_day_of_month = (next_month - timedelta(days=1)).day
            period_start = datetime(current_year, current_month, min(
                billing_day, last_day_of_month)).date()

        # Si la fecha de referencia es anterior al inicio del período actual,
        # el período actual es el anterior
        if reference_date < period_start:
            period_start = period_start - relativedelta(months=1)

        # Calcular el final del período (un mes después - 1 día)
        period_end = period_start + relativedelta(months=1) - timedelta(days=1)

        return period_start, period_end

    @classmethod
    def calculate_period_from_record(cls, usage_record):
        """
        Calcula el período basado en un registro existente
        """
        # Para registros existentes, asumir mes calendario
        from datetime import datetime
        return cls.calculate_calendar_month_period(
            datetime(usage_record.year, usage_record.month, 15).date()
        )

    @classmethod
    def get_current_usage(cls, user):
        """
        Obtiene el uso actual del usuario para este mes
        """
        usage, _ = cls.get_or_create_current(user)
        return usage

    def can_add_expense(self):
        """
        Verifica si el usuario puede agregar un gasto este mes
        """
        if not self.user.subscription_plan:
            return False, "No tienes un plan asignado"

        # Si el plan tiene registros ilimitados
        if self.user.subscription_plan.unlimited_records:
            return True, ""

        # Obtener límites del plan
        from .plan_limits import get_max_expenses
        max_expenses = get_max_expenses(self.user.subscription_plan.name)

        if max_expenses is None:  # Ilimitado
            return True, ""

        if self.expenses_count >= max_expenses:
            from .plan_limits import get_upgrade_message
            message = get_upgrade_message(
                self.user.subscription_plan.name, 'expense')
            return False, message

        return True, ""

    def can_add_income(self):
        """
        Verifica si el usuario puede agregar un ingreso este mes
        """
        if not self.user.subscription_plan:
            return False, "No tienes un plan asignado"

        # Si el plan tiene registros ilimitados
        if self.user.subscription_plan.unlimited_records:
            return True, ""

        # Obtener límites del plan
        from .plan_limits import get_max_incomes
        max_incomes = get_max_incomes(self.user.subscription_plan.name)

        if max_incomes is None:  # Ilimitado
            return True, ""

        if self.incomes_count >= max_incomes:
            from .plan_limits import get_upgrade_message
            message = get_upgrade_message(
                self.user.subscription_plan.name, 'income')
            return False, message

        return True, ""

    def increment_expenses(self):
        """Incrementa el contador de gastos"""
        self.expenses_count += 1
        self.save(update_fields=['expenses_count', 'updated_at'])

    def increment_incomes(self):
        """Incrementa el contador de ingresos"""
        self.incomes_count += 1
        self.save(update_fields=['incomes_count', 'updated_at'])

    def decrement_expenses(self):
        """Decrementa el contador de gastos"""
        if self.expenses_count > 0:
            self.expenses_count -= 1
            self.save(update_fields=['expenses_count', 'updated_at'])

    def decrement_incomes(self):
        """Decrementa el contador de ingresos"""
        if self.incomes_count > 0:
            self.incomes_count -= 1
            self.save(update_fields=['incomes_count', 'updated_at'])

    def get_usage_summary(self):
        """
        Retorna un resumen del uso del período actual
        """
        from .plan_limits import get_plan_limits
        limits = get_plan_limits(
            self.user.subscription_plan.name if self.user.subscription_plan else 'BASIC')

        # Calcular el período real del usuario
        period_start, period_end = self.calculate_user_billing_period(
            self.user)

        # Determinar tipo de período
        is_calendar_month = (period_start.day == 1)
        period_type = "Mes calendario" if is_calendar_month else "Período de aniversario"

        return {
            'period': f"{period_start.strftime('%Y-%m-%d')} a {period_end.strftime('%Y-%m-%d')}",
            'period_type': period_type,
            'days_remaining': max(0, (period_end - timezone.now().date()).days + 1) if period_end >= timezone.now().date() else 0,
            'expenses': {
                'used': self.expenses_count,
                'limit': limits.get('max_expenses'),
                'remaining': None if limits.get('max_expenses') is None else max(0, limits.get('max_expenses') - self.expenses_count)
            },
            'incomes': {
                'used': self.incomes_count,
                'limit': limits.get('max_incomes'),
                'remaining': None if limits.get('max_incomes') is None else max(0, limits.get('max_incomes') - self.incomes_count)
            }
        }
