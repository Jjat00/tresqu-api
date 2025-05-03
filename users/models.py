from django.db import models


class User(models.Model):
    external_id = models.CharField(max_length=100, unique=True)
    platform = models.CharField(max_length=20, choices=[(
        "telegram", "Telegram"), ("whatsapp", "WhatsApp")])
    first_name = models.CharField(max_length=100, blank=True)
    username = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(
        max_length=20, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
