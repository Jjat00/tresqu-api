from django.contrib import admin
from .models import GoogleAccount, GmailWatch, ProcessedEmail


@admin.register(GoogleAccount)
class GoogleAccountAdmin(admin.ModelAdmin):
    list_display = ('user', 'google_email', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('google_email', 'user__first_name', 'user__external_id')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(GmailWatch)
class GmailWatchAdmin(admin.ModelAdmin):
    list_display = ('google_account', 'history_id', 'watch_expiration', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ProcessedEmail)
class ProcessedEmailAdmin(admin.ModelAdmin):
    list_display = ('subject', 'sender', 'google_account', 'is_purchase', 'processing_status', 'awaiting_categorization', 'received_at')
    list_filter = ('processing_status', 'is_purchase', 'awaiting_categorization')
    search_fields = ('subject', 'sender', 'gmail_message_id')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('google_account', 'expense')
