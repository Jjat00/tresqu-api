from django.db import migrations


def create_predefined_categories(apps, schema_editor):
    """
    Crea las nuevas categorías predefinidas en la base de datos con descripciones y ejemplos.
    """
    Category = apps.get_model('categories', 'Category')

    # Primero eliminamos todas las categorías existentes
    Category.objects.all().delete()

    # Datos de las nuevas categorías
    categories_data = [
        {
            'name': 'Vivienda',
            'description': 'Gastos relacionados con el lugar donde se vive.',
            'examples': 'Arriendo, hipoteca, servicios públicos, mantenimiento',
            'color': '#1E3A8A'
        },
        {
            'name': 'Alimentación',
            'description': 'Comida y bebidas no alcohólicas.',
            'examples': 'Mercado, restaurantes, cafeterías, delivery',
            'color': '#10B981'
        },
        {
            'name': 'Transporte y Movilidad',
            'description': 'Gastos para moverse o mantener un vehículo.',
            'examples': 'Transporte público, combustible, mantenimiento',
            'color': '#F97316'
        },
        {
            'name': 'Préstamos',
            'description': 'Salida de dinero por prestamo.',
            'examples': 'Tarjetas de crédito, préstamos, gota a gota',
            'color': '#DC2626'
        },
        {
            'name': 'Salud y Bienestar',
            'description': 'Gastos que cuidan el cuerpo y la mente.',
            'examples': 'Medicina, EPS, gimnasio, terapia',
            'color': '#8B5CF6'
        },
        {
            'name': 'Educación y Formación',
            'description': 'Gastos para aprender o mejorar habilidades.',
            'examples': 'Matrículas, cursos, libros',
            'color': '#FACC15'
        },
        {
            'name': 'Ropa',
            'description': 'Gastos relacionados con prendas de vestir.',
            'examples': 'Ropa, calzado, accesorios',
            'color': '#3B82F6'
        },
        {
            'name': 'Cuidado Personal',
            'description': 'Bienestar físico e imagen.',
            'examples': 'Peluquería, productos de higiene, estética',
            'color': '#38BDF8'
        },
        {
            'name': 'Compras',
            'description': 'Bienes no recurrentes o de consumo duradero.',
            'examples': 'Electrodomésticos, tecnología, hobbies',
            'color': '#78350F'
        },
        {
            'name': 'Entretenimiento y Ocio',
            'description': 'Actividades recreativas en casa o fuera.',
            'examples': 'Streaming, cine, videojuegos',
            'color': '#DB2777'
        },
        {
            'name': 'Viajes y Salidas',
            'description': 'Escapadas o turismo.',
            'examples': 'Hoteles, pasajes, turismo',
            'color': '#FB923C'
        },
        {
            'name': 'Bebidas Alcohólicas y Fiestas',
            'description': 'Consumo de licor o fiestas sociales.',
            'examples': 'Bares, licores, celebraciones',
            'color': '#991B1B'
        },
        {
            'name': 'Familia y Dependientes',
            'description': 'Gastos asociados al núcleo familiar.',
            'examples': 'Cuidado infantil, educación hijos',
            'color': '#166534'
        },
        {
            'name': 'Mascotas',
            'description': 'Cuidado y bienestar animal.',
            'examples': 'Veterinario, comida, juguetes',
            'color': '#92400E'
        },
        {
            'name': 'Regalos y Donaciones',
            'description': 'Aportes emocionales o solidarios.',
            'examples': 'Regalos, ayudas, donaciones',
            'color': '#F472B6'
        },
        {
            'name': 'Suscripciones y Membresías',
            'description': 'Pagos recurrentes por servicios digitales.',
            'examples': 'Netflix, Spotify, Amazon',
            'color': '#0EA5E9'
        }
    ]

    # Creamos las nuevas categorías
    for cat_data in categories_data:
        Category.objects.create(**cat_data)


def reverse_migration(apps, schema_editor):
    """
    No eliminamos las categorías en caso de rollback para no afectar
    a los gastos que ya estén utilizando estas categorías.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('categories', '0006_category_description_category_examples'),
    ]

    operations = [
        migrations.RunPython(create_predefined_categories, reverse_migration),
    ]
