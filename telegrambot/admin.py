from django.contrib import admin
from .models import TelegramChat, TelegramMessage


@admin.register(TelegramChat)
class TelegramChatAdmin(admin.ModelAdmin):
    list_display = ('chat_id', 'user', 'created_at')
    search_fields = ('chat_id', 'user__username')
    list_filter = ('created_at',)


@admin.register(TelegramMessage)
class TelegramMessageAdmin(admin.ModelAdmin):
    list_display = ('chat', 'message_type', 'text', 'created_at')
    search_fields = ('text', 'chat__chat_id')
    list_filter = ('message_type', 'created_at')
