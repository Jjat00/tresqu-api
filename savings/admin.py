from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum
from .models import SavingsCategory, SavingsGoal, SavingsDeposit, SavingsTemplate


@admin.register(SavingsCategory)
class SavingsCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'color_display',
                    'is_default', 'is_active', 'goals_count', 'created_at']
    list_filter = ['is_active', 'is_default', 'created_at', 'user']
    search_fields = ['name', 'description', 'user__username', 'user__email']
    ordering = ['user', 'name']

    def color_display(self, obj):
        return format_html(
            '<span style="color: {}; font-weight: bold;">● {}</span>',
            obj.color,
            obj.color
        )
    color_display.short_description = 'Color'

    def goals_count(self, obj):
        return obj.savingsgoal_set.count()
    goals_count.short_description = 'Metas asociadas'


@admin.register(SavingsGoal)
class SavingsGoalAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'user', 'category', 'current_amount', 'target_amount',
        'progress_percentage', 'status', 'priority', 'target_date'
    ]
    list_filter = ['status', 'priority', 'category',
                   'auto_save_enabled', 'created_at']
    search_fields = ['name', 'description', 'user__username', 'user__email']
    ordering = ['-created_at']
    readonly_fields = ['current_amount', 'progress_percentage',
                       'remaining_amount', 'is_completed']

    fieldsets = (
        ('Información Básica', {
            'fields': ('user', 'name', 'description', 'category')
        }),
        ('Metas Financieras', {
            'fields': ('target_amount', 'current_amount', 'currency', 'target_date')
        }),
        ('Estado y Prioridad', {
            'fields': ('status', 'priority')
        }),
        ('Ahorro Automático', {
            'fields': ('auto_save_enabled', 'auto_save_amount', 'auto_save_frequency'),
            'classes': ('collapse',)
        }),
        ('Información Calculada', {
            'fields': ('progress_percentage', 'remaining_amount', 'is_completed'),
            'classes': ('collapse',)
        }),
        ('Metadatos', {
            'fields': ('notes', 'completed_at'),
            'classes': ('collapse',)
        })
    )

    def progress_percentage(self, obj):
        percentage = obj.progress_percentage
        color = 'green' if percentage >= 100 else 'orange' if percentage >= 50 else 'red'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{:.1f}%</span>',
            color,
            percentage
        )
    progress_percentage.short_description = 'Progreso'


class SavingsDepositInline(admin.TabularInline):
    model = SavingsDeposit
    fields = ['amount', 'description',
              'transaction_type', 'timestamp', 'source']
    readonly_fields = ['timestamp']
    extra = 0
    ordering = ['-timestamp']


@admin.register(SavingsDeposit)
class SavingsDepositAdmin(admin.ModelAdmin):
    list_display = [
        'savings_goal', 'amount_display', 'transaction_type',
        'timestamp', 'source', 'is_deposit'
    ]
    list_filter = ['transaction_type', 'timestamp', 'savings_goal__category']
    search_fields = [
        'savings_goal__name', 'description', 'source',
        'savings_goal__user__username'
    ]
    ordering = ['-timestamp']
    readonly_fields = ['is_deposit', 'is_withdrawal']

    fieldsets = (
        ('Información Principal', {
            'fields': ('savings_goal', 'amount', 'description', 'transaction_type')
        }),
        ('Detalles Adicionales', {
            'fields': ('source', 'notes'),
            'classes': ('collapse',)
        }),
        ('Información del Sistema', {
            'fields': ('is_deposit', 'is_withdrawal', 'timestamp'),
            'classes': ('collapse',)
        })
    )

    def amount_display(self, obj):
        color = 'green' if obj.amount > 0 else 'red'
        symbol = '+' if obj.amount > 0 else ''
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}{}</span>',
            color,
            symbol,
            obj.amount
        )
    amount_display.short_description = 'Monto'


@admin.register(SavingsTemplate)
class SavingsTemplateAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'category', 'suggested_amount_range', 'suggested_timeframe_months',
        'priority', 'is_active'
    ]
    list_filter = ['category', 'priority', 'is_active', 'created_at']
    search_fields = ['name', 'description', 'tips']
    ordering = ['category', 'name']

    fieldsets = (
        ('Información Básica', {
            'fields': ('name', 'description', 'category')
        }),
        ('Valores Sugeridos', {
            'fields': (
                'suggested_amount_min', 'suggested_amount_max',
                'suggested_timeframe_months', 'priority'
            )
        }),
        ('Configuración', {
            'fields': ('is_active',)
        }),
        ('Consejos', {
            'fields': ('tips',),
            'classes': ('collapse',)
        })
    )

    def suggested_amount_range(self, obj):
        if obj.suggested_amount_min and obj.suggested_amount_max:
            return f"${obj.suggested_amount_min:,.2f} - ${obj.suggested_amount_max:,.2f}"
        elif obj.suggested_amount_min:
            return f"Desde ${obj.suggested_amount_min:,.2f}"
        elif obj.suggested_amount_max:
            return f"Hasta ${obj.suggested_amount_max:,.2f}"
        return "No definido"
    suggested_amount_range.short_description = 'Rango sugerido'


# Agregar inline de depósitos a las metas de ahorro
SavingsGoalAdmin.inlines = [SavingsDepositInline]
