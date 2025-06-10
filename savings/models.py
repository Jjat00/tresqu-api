from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from decimal import Decimal
import uuid


class SavingsCategory(models.Model):
    """Categorías de ahorro para organizar las metas financieras"""

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="Usuario")
    name = models.CharField(max_length=100, verbose_name="Nombre")
    description = models.TextField(
        blank=True, null=True, verbose_name="Descripción")
    color = models.CharField(
        max_length=7, default='#4CAF50', verbose_name="Color")
    icon = models.CharField(max_length=50, blank=True,
                            null=True, verbose_name="Icono")
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    is_default = models.BooleanField(
        default=False, verbose_name="Categoría predefinida")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoría de Ahorro"
        verbose_name_plural = "Categorías de Ahorro"
        ordering = ['user', 'name']
        # Un usuario no puede tener dos categorías con el mismo nombre
        unique_together = ['user', 'name']

    def __str__(self):
        return f"{self.user.username} - {self.name}"


class SavingsGoal(models.Model):
    """Metas de ahorro del usuario"""

    PRIORITY_CHOICES = [
        ('low', 'Baja'),
        ('medium', 'Media'),
        ('high', 'Alta'),
        ('urgent', 'Urgente'),
    ]

    STATUS_CHOICES = [
        ('active', 'Activa'),
        ('completed', 'Completada'),
        ('paused', 'Pausada'),
        ('cancelled', 'Cancelada'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="Usuario")
    category = models.ForeignKey(
        SavingsCategory, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Categoría")

    name = models.CharField(max_length=200, verbose_name="Nombre de la meta")
    description = models.TextField(
        blank=True, null=True, verbose_name="Descripción")

    # Metas financieras
    target_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Monto objetivo"
    )
    current_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name="Monto actual"
    )
    currency = models.CharField(
        max_length=3, default='COP', verbose_name="Moneda")

    # Fechas
    target_date = models.DateField(
        verbose_name="Fecha objetivo", null=True, blank=True)
    created_at = models.DateTimeField(
        auto_now_add=True, verbose_name="Fecha de creación")
    completed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Fecha de completado")

    # Estado y prioridad
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='active', verbose_name="Estado")
    priority = models.CharField(
        max_length=20, choices=PRIORITY_CHOICES, default='medium', verbose_name="Prioridad")

    # Configuración automática
    auto_save_enabled = models.BooleanField(
        default=False, verbose_name="Ahorro automático habilitado")
    auto_save_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Monto de ahorro automático"
    )
    auto_save_frequency = models.CharField(
        max_length=20,
        choices=[
            ('daily', 'Diario'),
            ('weekly', 'Semanal'),
            ('biweekly', 'Quincenal'),
            ('monthly', 'Mensual'),
        ],
        null=True,
        blank=True,
        verbose_name="Frecuencia de ahorro automático"
    )

    # Metadatos
    notes = models.TextField(blank=True, null=True,
                             verbose_name="Notas adicionales")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Meta de Ahorro"
        verbose_name_plural = "Metas de Ahorro"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - ${self.current_amount}/{self.target_amount}"

    @property
    def progress_percentage(self):
        """Calcula el porcentaje de progreso hacia la meta"""
        if self.target_amount <= 0:
            return 0
        percentage = (self.current_amount / self.target_amount) * 100
        return min(percentage, 100)  # No más del 100%

    @property
    def remaining_amount(self):
        """Calcula el monto restante para completar la meta"""
        remaining = self.target_amount - self.current_amount
        return max(remaining, Decimal('0.00'))

    @property
    def is_completed(self):
        """Verifica si la meta está completada"""
        return self.current_amount >= self.target_amount

    @property
    def days_to_target(self):
        """Calcula los días restantes hasta la fecha objetivo"""
        if not self.target_date:
            return None
        today = timezone.now().date()
        if self.target_date <= today:
            return 0
        return (self.target_date - today).days

    @property
    def recommended_daily_saving(self):
        """Calcula el ahorro diario recomendado para alcanzar la meta"""
        if not self.target_date or self.is_completed:
            return Decimal('0.00')

        days_remaining = self.days_to_target
        if days_remaining <= 0:
            return self.remaining_amount

        return self.remaining_amount / days_remaining

    def add_deposit(self, amount, description="", transaction_type="manual"):
        """Agrega un depósito a esta meta de ahorro"""
        deposit = SavingsDeposit.objects.create(
            savings_goal=self,
            amount=amount,
            description=description,
            transaction_type=transaction_type
        )

        # Actualizar el monto actual
        self.current_amount += amount

        # Marcar como completada si se alcanzó la meta
        if self.is_completed and self.status == 'active':
            self.status = 'completed'
            self.completed_at = timezone.now()

        self.save()
        return deposit

    def withdraw(self, amount, description="Retiro"):
        """Retira dinero de esta meta de ahorro"""
        if amount > self.current_amount:
            raise ValueError("No se puede retirar más dinero del disponible")

        withdrawal = SavingsDeposit.objects.create(
            savings_goal=self,
            amount=-amount,  # Monto negativo para retiros
            description=description,
            transaction_type="withdrawal"
        )

        # Actualizar el monto actual
        self.current_amount -= amount

        # Si estaba completada pero ya no, cambiar el estado
        if not self.is_completed and self.status == 'completed':
            self.status = 'active'
            self.completed_at = None

        self.save()
        return withdrawal


class SavingsDeposit(models.Model):
    """Registro de depósitos y retiros en las metas de ahorro"""

    TRANSACTION_TYPES = [
        ('manual', 'Manual'),
        ('automatic', 'Automático'),
        ('transfer', 'Transferencia'),
        ('withdrawal', 'Retiro'),
        ('interest', 'Interés'),
        ('bonus', 'Bono'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    savings_goal = models.ForeignKey(
        SavingsGoal,
        on_delete=models.CASCADE,
        related_name='deposits',
        verbose_name="Meta de ahorro"
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name="Monto"
    )  # Puede ser negativo para retiros

    description = models.CharField(
        max_length=255, blank=True, null=True, verbose_name="Descripción")
    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPES,
        default='manual',
        verbose_name="Tipo de transacción"
    )

    # Metadatos
    timestamp = models.DateTimeField(
        auto_now_add=True, verbose_name="Fecha y hora")
    updated_at = models.DateTimeField(auto_now=True)

    # Información adicional
    source = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Origen del depósito"
    )
    notes = models.TextField(blank=True, null=True, verbose_name="Notas")

    class Meta:
        verbose_name = "Depósito de Ahorro"
        verbose_name_plural = "Depósitos de Ahorro"
        ordering = ['-timestamp']

    def __str__(self):
        action = "Depósito" if self.amount >= 0 else "Retiro"
        return f"{action} de ${abs(self.amount)} en {self.savings_goal.name}"

    @property
    def is_deposit(self):
        """Verifica si es un depósito (monto positivo)"""
        return self.amount > 0

    @property
    def is_withdrawal(self):
        """Verifica si es un retiro (monto negativo)"""
        return self.amount < 0


class SavingsTemplate(models.Model):
    """Plantillas predefinidas para metas de ahorro comunes"""

    name = models.CharField(
        max_length=200, verbose_name="Nombre de la plantilla")
    description = models.TextField(verbose_name="Descripción")
    category = models.ForeignKey(
        SavingsCategory,
        on_delete=models.CASCADE,
        verbose_name="Categoría"
    )

    # Valores sugeridos
    suggested_amount_min = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Monto mínimo sugerido"
    )
    suggested_amount_max = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Monto máximo sugerido"
    )
    suggested_timeframe_months = models.IntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
        verbose_name="Plazo sugerido en meses"
    )

    # Configuración
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    priority = models.CharField(
        max_length=20,
        choices=SavingsGoal.PRIORITY_CHOICES,
        default='medium',
        verbose_name="Prioridad sugerida"
    )

    # Consejos y recomendaciones
    tips = models.TextField(blank=True, null=True, verbose_name="Consejos")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Plantilla de Ahorro"
        verbose_name_plural = "Plantillas de Ahorro"
        ordering = ['category', 'name']

    def __str__(self):
        return f"{self.name} ({self.category.name})"
