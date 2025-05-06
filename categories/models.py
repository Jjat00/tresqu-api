from django.db import models


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

    name = models.CharField(max_length=100, unique=True)
    metadata = models.JSONField(blank=True, null=True)

    @classmethod
    def get_all_categories(cls):
        """Retorna todas las categorías existentes"""
        return list(cls.objects.values_list('name', flat=True))

    def __str__(self):
        return self.name
