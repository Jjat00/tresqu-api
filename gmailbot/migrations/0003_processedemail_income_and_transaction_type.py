from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('gmailbot', '0002_processedemail_notification_message_id'),
        ('income', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='processedemail',
            name='transaction_type',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
        migrations.AddField(
            model_name='processedemail',
            name='income',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='processed_emails',
                to='income.income',
            ),
        ),
    ]
