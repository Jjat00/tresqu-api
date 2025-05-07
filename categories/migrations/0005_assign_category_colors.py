from django.db import migrations

# Definimos los colores predefinidos aquí para usarlos en la migración
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
    'Bebidas alcohólicas': '#B33DC6',
    'Compras': '#FF6384',
    'Dulces': '#FF9F40'
}


def assign_category_colors(apps, schema_editor):
    """
    Asigna los colores predefinidos a las categorías existentes
    """
    Category = apps.get_model('categories', 'Category')

    # Obtener todas las categorías existentes
    categories = Category.objects.all()

    # Actualizar cada categoría con su color predefinido
    for category in categories:
        if category.name in DEFAULT_COLORS:
            category.color = DEFAULT_COLORS[category.name]
            category.save()


def reverse_assign_category_colors(apps, schema_editor):
    """
    Revierte los cambios estableciendo el color por defecto
    """
    Category = apps.get_model('categories', 'Category')
    Category.objects.all().update(color='#CCCCCC')


class Migration(migrations.Migration):
    dependencies = [
        ('categories', '0004_add_category_color'),
    ]

    operations = [
        migrations.RunPython(assign_category_colors,
                             reverse_assign_category_colors),
    ]
