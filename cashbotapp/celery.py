import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cashbotapp.settings")

app = Celery("cashbotapp")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
# `gmailbot.composio_tasks` (y futuros módulos con sufijo Composio) no son
# descubiertos por el autodiscover default que solo busca `tasks.py`.
app.autodiscover_tasks(related_name="composio_tasks")
