"""Migración Gmail → Composio (fase 5).

Elimina los modelos legacy ``GoogleAccount`` y ``GmailWatch`` junto con
sus tablas. La fase 4 (0004) ya había desacoplado ``ProcessedEmail`` de
estos modelos, dejando como FK durable ``ProcessedEmail.user``.

Orden importante: ``GmailWatch`` se borra antes que ``GoogleAccount``
porque tenía un ``OneToOneField`` apuntando a éste.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [('gmailbot', '0004_composio_connection_and_fk_swap')]

    operations = [
        migrations.DeleteModel(name='GmailWatch'),
        migrations.DeleteModel(name='GoogleAccount'),
    ]
