from django.contrib import admin
from .models import Expense


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'currency',
                    'get_category_display', 'description', 'timestamp', 'spent_at')
    list_filter = ('currency', 'timestamp', 'spent_at',
                   'category', 'user_expense_category')
    search_fields = ('description', 'note', 'raw_message',
                     'user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at', 'embedding')
    date_hierarchy = 'timestamp'
    ordering = ('-timestamp',)

    fieldsets = (
        ('Información Básica', {
            'fields': ('user', 'amount', 'currency', 'description', 'timestamp', 'spent_at')
        }),
        ('Categorización', {
            'fields': ('category', 'category_str', 'user_expense_category'),
            'description': 'Campos de categorización (category es el campo original, user_expense_category es el nuevo)'
        }),
        ('Detalles Adicionales', {
            'fields': ('note', 'raw_message'),
            'classes': ('collapse',)
        }),
        ('Metadatos', {
            'fields': ('embedding', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def get_category_display(self, obj):
        """Muestra la categoría prioritizando user_expense_category"""
        if obj.user_expense_category:
            return f"{obj.user_expense_category.name} (Usuario)"
        elif obj.category:
            return f"{obj.category.name} (Sistema)"
        elif obj.category_str:
            return f"{obj.category_str} (String)"
        return "Sin categoría"
    get_category_display.short_description = 'Categoría'
    get_category_display.admin_order_field = 'user_expense_category__name'
