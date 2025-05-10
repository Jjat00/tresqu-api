from django.contrib import admin
from users.models import Chat, Message


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ('platform', 'platform_chat_id', 'user', 'created_at')
    search_fields = ('platform_chat_id', 'user__username')
    list_filter = ('platform', 'created_at')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('chat', 'message_type', 'text', 'created_at')
    search_fields = ('text', 'chat__platform_chat_id')
    list_filter = ('message_type', 'created_at')
