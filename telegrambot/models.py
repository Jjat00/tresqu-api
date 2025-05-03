from django.db import models
from users.models import User

# Create your models here.


class TelegramChat(models.Model):
    chat_id = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Chat {self.chat_id} - {self.user.username if self.user else 'No User'}"


class TelegramMessage(models.Model):
    MESSAGE_TYPE_CHOICES = [
        ('incoming', 'Incoming'),
        ('outgoing', 'Outgoing'),
    ]

    chat = models.ForeignKey(
        TelegramChat, on_delete=models.CASCADE, related_name='messages')
    message_id = models.CharField(max_length=100)
    message_type = models.CharField(
        max_length=10, choices=MESSAGE_TYPE_CHOICES)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.message_type.capitalize()} - {self.text[:50]}"
