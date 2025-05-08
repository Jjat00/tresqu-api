from django.db import models
from pgvector.django import VectorField, CosineDistance, HnswIndex
from users.models import User
from categories.models import Category
import random


class IncomeCategory(models.Model):
    # Categorías predefinidas para ingresos
    PREDEFINED_CATEGORIES = [
        'Salario', 'Inversiones', 'Intereses', 'Dividendos', 'Alquiler',
        'Freelance', 'Reembolsos', 'Ventas', 'Regalos', 'Premios',
        'Pensión', 'Subsidios', 'Bonos', 'Comisiones', 'Becas'
    ]

    # Colores predefinidos para las categorías
    DEFAULT_COLORS = {
        'Salario': '#36A2EB',
        'Inversiones': '#FFCE56',
        'Intereses': '#4BC0C0',
        'Dividendos': '#9966FF',
        'Alquiler': '#FF9F40',
        'Freelance': '#8AC249',
        'Reembolsos': '#EA5545',
        'Ventas': '#F46A9B',
        'Regalos': '#EF9B20',
        'Premios': '#EDBF33',
        'Pensión': '#87BC45',
        'Subsidios': '#27AEEF',
        'Bonos': '#B33DC6',
        'Comisiones': '#FF6384',
        'Becas': '#4BC0C0'
    }

    # Paleta de colores para nuevas categorías
    COLOR_PALETTE = [
        '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF',
        '#FF9F40', '#8AC249', '#EA5545', '#F46A9B', '#EF9B20',
        '#EDBF33', '#87BC45', '#27AEEF', '#B33DC6', '#FF6384'
    ]

    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(
        max_length=7, default='#CCCCCC')  # Formato hexadecimal
    metadata = models.JSONField(blank=True, null=True)

    @classmethod
    def get_all_categories(cls):
        """Retorna todas las categorías existentes"""
        return list(cls.objects.values_list('name', flat=True))

    @classmethod
    def get_default_color(cls, category_name):
        """Retorna el color por defecto para una categoría"""
        return cls.DEFAULT_COLORS.get(category_name, '#CCCCCC')

    @classmethod
    def get_unused_color(cls):
        """Retorna un color no utilizado de la paleta"""
        used_colors = set(cls.objects.values_list('color', flat=True))
        available_colors = [
            color for color in cls.COLOR_PALETTE if color not in used_colors]

        if available_colors:
            return random.choice(available_colors)

        # Si todos los colores están en uso, generar uno aleatorio
        return f'#{random.randint(0, 0xFFFFFF):06x}'

    def save(self, *args, **kwargs):
        """Sobrescribe el método save para asignar un color por defecto si no se proporciona uno"""
        if not self.color or self.color == '#CCCCCC':
            # Primero intentar con el color predefinido
            default_color = self.get_default_color(self.name)
            if default_color != '#CCCCCC':
                self.color = default_color
            else:
                # Si no hay color predefinido, buscar uno no utilizado
                self.color = self.get_unused_color()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Income(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='incomes')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default='COP')
    category = models.ForeignKey(
        IncomeCategory, on_delete=models.SET_NULL, null=True, blank=True)
    category_str = models.CharField(
        max_length=100, blank=True, null=True)
    description = models.TextField(blank=True)
    timestamp = models.DateTimeField()
    received_at = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    raw_message = models.TextField(blank=True)
    embedding = VectorField(dimensions=1536, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            HnswIndex(
                name='income_embedding_idx',
                fields=['embedding'],
                m=16,                     # Cantidad de conexiones por nodo
                ef_construction=64,       # Factor de exploración durante construcción
                # Operador de distancia coseno
                opclasses=['vector_cosine_ops'],
            ),
        ]

    def __str__(self):
        category_name = self.category.name if self.category else self.category_str
        return f"{category_name}: {self.amount} {self.currency} ({self.timestamp})"

    @classmethod
    def find_similar(cls, user, embedding, limit=5):
        """
        Encuentra ingresos similares basados en el embedding proporcionado
        """
        if not embedding:
            return cls.objects.none()

        return cls.objects.filter(
            user=user,
            embedding__isnull=False
        ).annotate(
            distance=CosineDistance('embedding', embedding)
        ).order_by('distance')[:limit]
