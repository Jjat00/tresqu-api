from django.db import models
from users.models import User


class GoogleAccount(models.Model):
    """
    Almacena las credenciales de Google OAuth2 de un usuario.
    Los tokens se almacenan cifrados con Fernet.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='google_account'
    )
    google_email = models.CharField(max_length=255)
    access_token_encrypted = models.BinaryField()
    refresh_token_encrypted = models.BinaryField()
    token_expiry = models.DateTimeField(null=True, blank=True)
    scopes = models.TextField(
        default='https://www.googleapis.com/auth/gmail.readonly'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cuenta de Google'
        verbose_name_plural = 'Cuentas de Google'

    def __str__(self):
        return f"{self.user} - {self.google_email}"


class GmailWatch(models.Model):
    """
    Almacena la información de la suscripción push de Gmail (Pub/Sub).
    """
    google_account = models.OneToOneField(
        GoogleAccount,
        on_delete=models.CASCADE,
        related_name='watch'
    )
    history_id = models.CharField(max_length=255, null=True, blank=True)
    watch_expiration = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Gmail Watch'
        verbose_name_plural = 'Gmail Watches'

    def __str__(self):
        return f"Watch: {self.google_account.google_email} (active={self.is_active})"


class ProcessedEmail(models.Model):
    """
    Registro de cada email procesado por el sistema.
    Permite deduplicación y seguimiento del pipeline de procesamiento.
    """
    PROCESSING_STATUS_CHOICES = [
        ('pending', 'Pendiente'),
        ('processed', 'Procesado'),
        ('skipped', 'Omitido'),
        ('error', 'Error'),
    ]

    google_account = models.ForeignKey(
        GoogleAccount,
        on_delete=models.CASCADE,
        related_name='processed_emails'
    )
    gmail_message_id = models.CharField(max_length=255)
    subject = models.CharField(max_length=500, default='')
    sender = models.CharField(max_length=255, default='')
    received_at = models.DateTimeField(null=True, blank=True)
    is_purchase = models.BooleanField(default=False)
    expense = models.ForeignKey(
        'expenses.Expense',
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('google_account', 'gmail_message_id')
        verbose_name = 'Email Procesado'
        verbose_name_plural = 'Emails Procesados'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.subject[:60]} - {self.processing_status}"
