from django.db import models
import random


class Category(models.Model):
    # Mantenemos PREDEFINED_CATEGORIES para usarlo en la migración inicial
    # pero luego se usarán las categorías desde la base de datos
    PREDEFINED_CATEGORIES = [
        'Alimentación', 'Transporte', 'Vivienda', 'Entretenimiento', 'Salud',
        'Educación', 'Ropa', 'Tecnología', 'Servicios', 'Mascota', 'Belleza',
        'Deportes', 'Viajes', 'Regalo', 'Seguros', 'Impuestos', 'Libros',
        'Mobiliario', 'Electrodomésticos', 'Restaurante', 'Café', 'Supermercado',
        'Suscripciones', 'Hobbies', 'Ahorro', 'Inversión', 'Donación', 'Bebidas alcohólicas'
    ]

    # Colores predefinidos para las categorías
    DEFAULT_COLORS = {
        'Alimentación': '#FF6384',
        'Transporte': '#36A2EB',
        'Vivienda': '#FFCE56',
        'Entretenimiento': '#4BC0C0',
        'Salud': '#9966FF',
        'Educación': '#FF9F40',
        'Ropa': '#8AC249',
        'Tecnología': '#EA5545',
        'Servicios': '#F46A9B',
        'Mascota': '#EF9B20',
        'Belleza': '#EDBF33',
        'Deportes': '#87BC45',
        'Viajes': '#27AEEF',
        'Regalo': '#B33DC6',
        'Seguros': '#FF6384',
        'Impuestos': '#36A2EB',
        'Libros': '#FFCE56',
        'Mobiliario': '#4BC0C0',
        'Electrodomésticos': '#9966FF',
        'Restaurante': '#FF9F40',
        'Café': '#8AC249',
        'Supermercado': '#EA5545',
        'Suscripciones': '#F46A9B',
        'Hobbies': '#EF9B20',
        'Ahorro': '#EDBF33',
        'Inversión': '#87BC45',
        'Donación': '#27AEEF',
        'Bebidas alcohólicas': '#B33DC6'
    }

    # Paleta de colores para nuevas categorías
    COLOR_PALETTE = [
        '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF',
        '#FF9F40', '#8AC249', '#EA5545', '#F46A9B', '#EF9B20',
        '#EDBF33', '#87BC45', '#27AEEF', '#B33DC6', '#FF6384',
        '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40',
        '#8AC249', '#EA5545', '#F46A9B', '#EF9B20', '#EDBF33',
        '#87BC45', '#27AEEF', '#B33DC6'
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
