from django.contrib import admin

from .models import ComposioConnection, ProcessedEmail


@admin.register(ComposioConnection)
class ComposioConnectionAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'google_email',
        'connected_account_id',
        'status',
        'updated_at',
    )
    list_filter = ('status',)
    search_fields = (
        'google_email',
        'connected_account_id',
        'trigger_id',
        'user__first_name',
        'user__external_id',
    )
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ProcessedEmail)
class ProcessedEmailAdmin(admin.ModelAdmin):
    list_display = (
        'subject',
        'sender',
        'user',
        'is_purchase',
        'processing_status',
        'awaiting_categorization',
        'received_at',
    )
    list_filter = ('processing_status', 'is_purchase', 'awaiting_categorization')
    search_fields = ('subject', 'sender', 'gmail_message_id')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('user', 'composio_connection', 'expense', 'income')
