from django.db import models
from users.models import User


class ComposioConnection(models.Model):
    """Conexión Gmail de un usuario gestionada por Composio.

    Reemplaza a ``GoogleAccount`` (OAuth directo) en la migración a
    Composio. Los tokens y refresh quedan en Composio; aquí solo
    persistimos identificadores para correlacionar webhooks entrantes
    con el ``User`` y exponer estado en el dashboard.
    """

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_FAILED = 'failed'
    STATUS_DISCONNECTED = 'disconnected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente de autenticación'),
        (STATUS_ACTIVE, 'Activa'),
        (STATUS_FAILED, 'Fallida'),
        (STATUS_DISCONNECTED, 'Desconectada'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='composio_connection',
    )
    connected_account_id = models.CharField(
        max_length=128,
        unique=True,
        help_text='Composio connected_account_id (ca_xxx)',
    )
    trigger_id = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text='Composio trigger_id (ti_xxx) del GMAIL_NEW_GMAIL_MESSAGE',
    )
    google_email = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Conexión Composio'
        verbose_name_plural = 'Conexiones Composio'

    def __str__(self) -> str:
        return f"{self.user} - {self.connected_account_id} ({self.status})"


class ProcessedEmail(models.Model):
    """
    Registro de cada email procesado por el sistema.
    Permite deduplicación y seguimiento del pipeline de procesamiento.
    """
    PROCESSING_STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('processed', 'Procesado'),
        ('skipped', 'Omitido'),
        ('duplicate', 'Duplicado de otra transacción'),
        ('error', 'Error'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='processed_emails',
    )
    composio_connection = models.ForeignKey(
        ComposioConnection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='processed_emails',
    )
    gmail_message_id = models.CharField(max_length=255)
    subject = models.CharField(max_length=500, default='')
    sender = models.CharField(max_length=255, default='')
    received_at = models.DateTimeField(null=True, blank=True)
    is_purchase = models.BooleanField(default=False)
    # Tipo detectado por el LLM: "expense" | "income" | "none".
    # Se mantiene is_purchase para no romper callers existentes; este campo
    # agrega la distinción gasto vs ingreso.
    transaction_type = models.CharField(
        max_length=16, blank=True, default=''
    )
    expense = models.ForeignKey(
        'expenses.Expense',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='processed_emails'
    )
    income = models.ForeignKey(
        'income.Income',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='processed_emails'
    )
    processing_status = models.CharField(
        max_length=20,
        choices=PROCESSING_STATUS_CHOICES,
        default='pending'
    )
    ai_response = models.TextField(blank=True, default='')
    awaiting_categorization = models.BooleanField(default=False)
    # ID del mensaje de WhatsApp (Meta wamid) con la notificación de la compra.
    # Se usa para resolver respuestas del usuario con "quote" (swipe to reply)
    # y saber a qué gasto corresponde la respuesta.
    notification_message_id = models.CharField(
        max_length=255, blank=True, default='', db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Email Procesado'
        verbose_name_plural = 'Emails Procesados'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'composio_connection', 'gmail_message_id'],
                condition=models.Q(composio_connection__isnull=False),
                name='gmailbot_processedemail_user_conn_msg_uniq',
            ),
        ]

    def __str__(self):
        return f"{self.subject[:60]} - {self.processing_status}"
