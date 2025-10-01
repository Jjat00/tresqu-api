from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.conf import settings
from .models import (
    User, SubscriptionPlan, Subscription, Organization,
    OrganizationMembership, OrganizationInvitation,
    TrackingLink, TelegramVerification, MonthlyUsage
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


@admin.register(MonthlyUsage)
class MonthlyUsageAdmin(admin.ModelAdmin):
    list_display = [
        'user_display', 'period_display', 'expenses_count', 'incomes_count',
        'expenses_usage_display', 'incomes_usage_display', 'plan_display'
    ]
    list_filter = [
        'year', 'month', 'user__subscription_plan__name',
        ('user__subscription_plan', admin.RelatedOnlyFieldListFilter)
    ]
    search_fields = [
        'user__first_name', 'user__username', 'user__phone_number', 'user__external_id'
    ]
    readonly_fields = [
        'user', 'year', 'month', 'period_display', 'plan_display',
        'usage_summary_display', 'created_at', 'updated_at'
    ]

    fieldsets = (
        ('Usuario y Período', {
            'fields': ('user', 'period_display', 'plan_display')
        }),
        ('Contadores Mensuales', {
            'fields': ('expenses_count', 'incomes_count')
        }),
        ('Resumen de Uso', {
            'fields': ('usage_summary_display',),
            'description': 'Resumen detallado del uso mensual con límites'
        }),
        ('Fechas', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def user_display(self, obj):
        """Muestra información del usuario"""
        user = obj.user
        name = user.first_name or user.username or f"ID: {user.external_id[:10]}..."
        return format_html(
            '<strong>{}</strong><br><small style="color: #666;">{}</small>',
            name, user.platform.title()
        )
    user_display.short_description = "Usuario"

    def period_display(self, obj):
        """Muestra el período en formato legible"""
        return f"{obj.year}-{obj.month:02d}"
    period_display.short_description = "Período"

    def plan_display(self, obj):
        """Muestra el plan del usuario con color"""
        if not obj.user.subscription_plan:
            return format_html('<span style="color: #999;">Sin Plan</span>')

        plan_name = obj.user.subscription_plan.name
        colors = {
            'BASIC': '#FFA500',
            'PREMIUM': '#4CAF50',
            'BUSINESS': '#2196F3'
        }
        color = colors.get(plan_name, '#666')

        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, plan_name
        )
    plan_display.short_description = "Plan"

    def expenses_usage_display(self, obj):
        """Muestra el uso de gastos con barra de progreso"""
        if not obj.user.subscription_plan:
            return "N/A"

        from .plan_limits import get_max_expenses
        max_expenses = get_max_expenses(obj.user.subscription_plan.name)

        if max_expenses is None:
            return format_html(
                '<span style="color: #4CAF50;">∞ Ilimitado</span>'
            )

        percentage = (obj.expenses_count / max_expenses) * 100
        color = '#4CAF50' if percentage < 80 else '#FF9800' if percentage < 100 else '#F44336'

        return format_html(
            '<div style="display: flex; align-items: center;">'
            '<div style="width: 60px; height: 8px; background: #eee; border-radius: 4px; margin-right: 8px;">'
            '<div style="width: {}%; height: 100%; background: {}; border-radius: 4px;"></div>'
            '</div>'
            '<span style="color: {};">{}/{}</span>'
            '</div>',
            min(percentage, 100), color, color, obj.expenses_count, max_expenses
        )
    expenses_usage_display.short_description = "Uso Gastos"

    def incomes_usage_display(self, obj):
        """Muestra el uso de ingresos con barra de progreso"""
        if not obj.user.subscription_plan:
            return "N/A"

        from .plan_limits import get_max_incomes
        max_incomes = get_max_incomes(obj.user.subscription_plan.name)

        if max_incomes is None:
            return format_html(
                '<span style="color: #4CAF50;">∞ Ilimitado</span>'
            )

        percentage = (obj.incomes_count / max_incomes) * 100
        color = '#4CAF50' if percentage < 80 else '#FF9800' if percentage < 100 else '#F44336'

        return format_html(
            '<div style="display: flex; align-items: center;">'
            '<div style="width: 60px; height: 8px; background: #eee; border-radius: 4px; margin-right: 8px;">'
            '<div style="width: {}%; height: 100%; background: {}; border-radius: 4px;"></div>'
            '</div>'
            '<span style="color: {};">{}/{}</span>'
            '</div>',
            min(percentage, 100), color, color, obj.incomes_count, max_incomes
        )
    incomes_usage_display.short_description = "Uso Ingresos"

    def usage_summary_display(self, obj):
        """Muestra un resumen completo del uso mensual"""
        summary = obj.get_usage_summary()

        expenses = summary['expenses']
        incomes = summary['incomes']

        def format_usage(data, type_name):
            if data['limit'] is None:
                return f"<strong>{type_name}:</strong> {data['used']} (Ilimitado)"
            else:
                remaining = data['remaining']
                color = '#4CAF50' if remaining > 10 else '#FF9800' if remaining > 0 else '#F44336'
                return f"<strong>{type_name}:</strong> {data['used']}/{data['limit']} " \
                    f"<span style='color: {color};'>({remaining} restantes)</span>"

        return format_html(
            '<div style="background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 10px 0;">'
            '<h4 style="margin: 0 0 10px 0; color: #333;">📊 Resumen del Período {}</h4>'
            '<div style="margin: 5px 0;">{}</div>'
            '<div style="margin: 5px 0;">{}</div>'
            '</div>',
            summary['period'],
            format_usage(expenses, "Gastos"),
            format_usage(incomes, "Ingresos")
        )
    usage_summary_display.short_description = "Resumen de Uso"

    def get_queryset(self, request):
        """Optimiza las consultas incluyendo datos relacionados"""
        return super().get_queryset(request).select_related(
            'user', 'user__subscription_plan'
        ).order_by('-year', '-month', 'user__first_name')


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        'first_name', 'username', 'platform', 'phone_number',
        'subscription_plan', 'current_usage_display', 'source_tracking_link', 'created_at'
    ]
    list_filter = [
        'platform', 'subscription_plan', 'source_tracking_link',
        'subscription_active', 'created_at'
    ]
    search_fields = ['first_name', 'username', 'phone_number', 'external_id']
    readonly_fields = ['external_id',
                       'current_usage_display', 'created_at', 'updated_at']

    def current_usage_display(self, obj):
        """Muestra el uso mensual actual del usuario"""
        try:
            monthly_usage = obj.get_current_monthly_usage()
            summary = monthly_usage.get_usage_summary()

            expenses = summary['expenses']
            incomes = summary['incomes']

            def format_compact_usage(data, emoji):
                if data['limit'] is None:
                    return f"{emoji} {data['used']} (∞)"
                else:
                    remaining = data['remaining']
                    color = 'green' if remaining > 10 else 'orange' if remaining > 0 else 'red'
                    return f"{emoji} <span style='color: {color};'>{data['used']}/{data['limit']}</span>"

            return format_html(
                '<div style="font-size: 12px;">'
                '{}<br>{}'
                '</div>',
                format_compact_usage(expenses, "💸"),
                format_compact_usage(incomes, "💰")
            )
        except Exception:
            return format_html('<span style="color: #999;">N/A</span>')
    current_usage_display.short_description = "Uso Mensual"

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
        ('Uso Mensual Actual', {
            'fields': ('current_usage_display',),
            'description': 'Resumen del uso en el mes actual'
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
