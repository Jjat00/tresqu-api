from django.db import models
from pgvector.django import VectorField
from users.models import User

# Create your models here.


class TelegramChat(models.Model):
    chat_id = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Chat {self.chat_id}"


class TelegramMessage(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ('incoming', 'Incoming'),
        ('outgoing', 'Outgoing'),
    ]

    chat = models.ForeignKey(
        TelegramChat, on_delete=models.CASCADE, related_name='messages')
    message_id = models.CharField(max_length=100)
    message_type = models.CharField(
        max_length=20, choices=MESSAGE_TYPE_CHOICES)
    text = models.TextField()
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_message_type_display()} message: {self.text[:50]}"
