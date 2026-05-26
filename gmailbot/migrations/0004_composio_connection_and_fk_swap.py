"""Migración Gmail → Composio (fase 2).

Crea ``ComposioConnection`` y reasigna el FK de ``ProcessedEmail`` desde
``GoogleAccount`` hacia ``User`` (durable) + ``ComposioConnection``
(auditoría). Se asume base limpia: el guard aborta si encuentra filas
en ``ProcessedEmail`` para evitar pérdida silenciosa.

Las tablas legacy ``GoogleAccount`` y ``GmailWatch`` se eliminan en la
fase 5 de la migración (junto con sus callers en ``oauth.py``,
``gmail_service.py`` y views Pub/Sub) — no en esta migración.

IMPORTANTE: aplicar con conexión directa a Postgres (puerto 5432),
NO vía pgbouncer transaction-mode (puerto 6543), porque la migración
mezcla DDL en una sola transacción.
"""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _refuse_if_data_exists(apps, schema_editor):
    """Aborta si hay filas en ProcessedEmail.

    Estamos a punto de TRUNCATE; queremos asegurarnos de que no haya
    datos reales que olvidemos respaldar. El usuario confirmó al iniciar
    la migración que aún no hay usuarios con Gmail conectado.
    """
    ProcessedEmail = apps.get_model('gmailbot', 'ProcessedEmail')
    count = ProcessedEmail.objects.count()
    if count > 0:
        raise RuntimeError(
            f"Refuse to TRUNCATE gmailbot_processedemail: {count} filas "
            "existentes. Revisar manualmente antes de continuar."
        )


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('gmailbot', '0003_processedemail_income_and_transaction_type'),
        ('users', '0015_monthlyusage'),
    ]

    operations = [
        migrations.CreateModel(
            name='ComposioConnection',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False)),
                ('connected_account_id', models.CharField(
                    help_text='Composio connected_account_id (ca_xxx)',
                    max_length=128, unique=True)),
                ('trigger_id', models.CharField(
                    blank=True, default='',
                    help_text='Composio trigger_id (ti_xxx) del GMAIL_NEW_GMAIL_MESSAGE',
                    max_length=128)),
                ('google_email', models.CharField(
                    blank=True, default='', max_length=255)),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pendiente de autenticación'),
                        ('active', 'Activa'),
                        ('failed', 'Fallida'),
                        ('disconnected', 'Desconectada'),
                    ],
                    default='pending', max_length=20)),
                ('last_error', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='composio_connection',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Conexión Composio',
                'verbose_name_plural': 'Conexiones Composio',
            },
        ),
        migrations.AddIndex(
            model_name='composioconnection',
            index=models.Index(
                fields=['updated_at'],
                condition=models.Q(status='pending'),
                name='gmailbot_compconn_pending_idx',
            ),
        ),
        migrations.RunPython(_refuse_if_data_exists, _noop),
        migrations.RunSQL(
            sql='TRUNCATE TABLE gmailbot_processedemail RESTART IDENTITY;',
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterUniqueTogether(
            name='processedemail',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='processedemail',
            name='google_account',
        ),
        migrations.AddField(
            model_name='processedemail',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='processed_emails',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='processedemail',
            name='composio_connection',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='processed_emails',
                to='gmailbot.composioconnection',
            ),
        ),
        migrations.AddConstraint(
            model_name='processedemail',
            constraint=models.UniqueConstraint(
                fields=['user', 'composio_connection', 'gmail_message_id'],
                condition=models.Q(composio_connection__isnull=False),
                name='gmailbot_processedemail_user_conn_msg_uniq',
            ),
        ),
    ]
