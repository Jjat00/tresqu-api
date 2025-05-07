from django.core.management.base import BaseCommand
from categories.models import Category


class Command(BaseCommand):
    help = 'Actualiza los colores de las categorías existentes'

    def handle(self, *args, **kwargs):
        # Definimos los colores predefinidos
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

        # Obtener todas las categorías
        categories = Category.objects.all()

        # Contador para estadísticas
        updated = 0
        not_found = 0

        # Actualizar cada categoría
        for category in categories:
            if category.name in DEFAULT_COLORS:
                category.color = DEFAULT_COLORS[category.name]
                category.save()
                updated += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Categoría "{category.name}" actualizada con color {category.color}'
                    )
                )
            else:
                not_found += 1
                self.stdout.write(
                    self.style.WARNING(
                        f'No se encontró color predefinido para la categoría "{category.name}"'
                    )
                )

        # Mostrar resumen
        self.stdout.write(
            self.style.SUCCESS(
                f'\nResumen:\n'
                f'- Categorías actualizadas: {updated}\n'
                f'- Categorías sin color predefinido: {not_found}'
            )
        )
