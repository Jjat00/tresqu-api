from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.conf import settings
from .models import (
    User, SubscriptionPlan, Subscription, Organization,
    OrganizationMembership, OrganizationInvitation,
    TrackingLink, TelegramVerification
)
from cashbotapp.settings import WHATSAPP_BOT_NUMBER


@admin.register(TrackingLink)
class TrackingLinkAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'code', 'total_registrations', 'total_clicks',
        'conversion_rate_display', 'is_active', 'is_expired_display', 'created_at'
    ]
    list_filter = ['is_active', 'created_at', 'expires_at']
    search_fields = ['name', 'code', 'description']
    readonly_fields = [
        'total_registrations', 'total_clicks', 'conversion_rate_display',
        'whatsapp_link_display', 'created_at', 'updated_at'
    ]
    fieldsets = (
        ('Información Básica', {
            'fields': ('name', 'code', 'description')
        }),
        ('Configuración', {
            'fields': ('is_active', 'expires_at')
        }),
        ('Enlaces Generados', {
            'fields': ('whatsapp_link_display',),
            'description': 'Enlaces listos para compartir'
        }),
        ('Estadísticas', {
            'fields': ('total_registrations', 'total_clicks', 'conversion_rate_display'),
            'classes': ('collapse',)
        }),
        ('Fechas', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def conversion_rate_display(self, obj):
        """Muestra la tasa de conversión con formato"""
        rate = obj.conversion_rate
        if rate == 0:
            return "0%"
        return f"{rate:.1f}%"
    conversion_rate_display.short_description = "Tasa de Conversión"

    def is_expired_display(self, obj):
        """Muestra si el enlace ha expirado"""
        if obj.is_expired:
            return format_html('<span style="color: red;">Expirado</span>')
        elif obj.expires_at:
            return format_html('<span style="color: green;">Activo hasta {}</span>',
                               obj.expires_at.strftime('%d/%m/%Y'))
        else:
            return format_html('<span style="color: green;">Sin expiración</span>')
    is_expired_display.short_description = "Estado"

    def whatsapp_link_display(self, obj):
        """Muestra los enlaces de WhatsApp y Telegram generados"""
        if obj.pk:  # Solo si el objeto ya existe
            # Enlace de WhatsApp
            bot_phone_number = WHATSAPP_BOT_NUMBER
            whatsapp_link = obj.get_whatsapp_link(bot_phone_number)

            # Enlace de Telegram
            bot_username = getattr(
                settings, 'TELEGRAM_BOT_USERNAME', 'TresquBot')
            telegram_link = obj.get_telegram_link(bot_username)

            return format_html(
                '<div style="margin: 10px 0;">'
                '<strong>📱 WhatsApp:</strong><br>'
                '<a href="{}" target="_blank" style="color: #25D366;">{}</a><br>'
                '<small style="color: #666;">Mensaje predefinido con código</small><br><br>'
                '<strong>📱 Telegram:</strong><br>'
                '<a href="{}" target="_blank" style="color: #0088cc;">{}</a><br>'
                '<small style="color: #666;">Deep link con parámetro /start</small><br><br>'
                '<small style="color: #999;">Código de referido: <strong>{}</strong></small>'
                '</div>',
                whatsapp_link, whatsapp_link,
                telegram_link, telegram_link,
                obj.code.upper()
            )
        return "Guarda primero para generar enlaces"
    whatsapp_link_display.short_description = "Enlaces Generados"

    def save_model(self, request, obj, form, change):
        """Genera código automáticamente si no se proporciona"""
        if not obj.code:
            obj.code = TrackingLink.generate_code(obj.name)
        super().save_model(request, obj, form, change)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        'first_name', 'username', 'platform', 'phone_number',
        'subscription_plan', 'source_tracking_link', 'created_at'
    ]
    list_filter = [
        'platform', 'subscription_plan', 'source_tracking_link',
        'subscription_active', 'created_at'
    ]
    search_fields = ['first_name', 'username', 'phone_number', 'external_id']
    readonly_fields = ['external_id', 'created_at', 'updated_at']

    fieldsets = (
        ('Información Personal', {
            'fields': ('external_id', 'platform', 'first_name', 'username', 'phone_number')
        }),
        ('Configuración', {
            'fields': ('default_currency', 'timezone')
        }),
        ('Suscripción', {
            'fields': (
                'subscription_plan', 'subscription_active',
                'subscription_start_date', 'subscription_end_date', 'is_yearly_billing'
            )
        }),
        ('Tracking', {
            'fields': ('source_tracking_link',),
            'description': 'Información sobre el origen del usuario'
        }),
        ('Fechas', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


# Registrar otros modelos existentes
@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ['name', 'price_monthly', 'price_yearly', 'is_active']
    list_filter = ['name', 'is_active']


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'plan', 'start_date',
                    'end_date', 'is_active', 'amount_paid']
    list_filter = ['plan', 'is_active', 'is_yearly_billing']
    search_fields = ['user__first_name', 'user__phone_number']


@admin.register(TelegramVerification)
class TelegramVerificationAdmin(admin.ModelAdmin):
    list_display = ['phone_number', 'verification_code',
                    'is_verified', 'created_at']
    list_filter = ['is_verified', 'created_at']
    search_fields = ['phone_number']
