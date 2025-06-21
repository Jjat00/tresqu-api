from django.db import models
from users.models import User
import random


class Category(models.Model):
    # Mantenemos PREDEFINED_CATEGORIES para usarlo en la migración inicial
    # pero luego se usarán las categorías desde la base de datos
    PREDEFINED_CATEGORIES = [
        'Vivienda', 'Alimentación', 'Transporte y Movilidad', 'Préstamos',
        'Salud y Bienestar', 'Educación y Formación', 'Ropa', 'Cuidado Personal',
        'Compras', 'Entretenimiento y Ocio', 'Viajes y Salidas',
        'Bebidas Alcohólicas y Fiestas', 'Familia y Dependientes', 'Mascotas',
        'Regalos y Donaciones', 'Suscripciones y Membresías'
    ]

    # Colores predefinidos para las categorías
    DEFAULT_COLORS = {
        'Vivienda': '#1E3A8A',
        'Alimentación': '#10B981',
        'Transporte y Movilidad': '#F97316',
        'Préstamos': '#DC2626',
        'Salud y Bienestar': '#8B5CF6',
        'Educación y Formación': '#FACC15',
        'Ropa': '#3B82F6',
        'Cuidado Personal': '#38BDF8',
        'Compras': '#78350F',
        'Entretenimiento y Ocio': '#DB2777',
        'Viajes y Salidas': '#FB923C',
        'Bebidas Alcohólicas y Fiestas': '#991B1B',
        'Familia y Dependientes': '#166534',
        'Mascotas': '#92400E',
        'Regalos y Donaciones': '#F472B6',
        'Suscripciones y Membresías': '#0EA5E9'
    }

    # Descripciones predefinidas para las categorías
    DEFAULT_DESCRIPTIONS = {
        'Vivienda': 'Gastos relacionados con el lugar donde se vive.',
        'Alimentación': 'Comida y bebidas no alcohólicas.',
        'Transporte y Movilidad': 'Gastos para moverse o mantener un vehículo.',
        'Préstamos': 'Salida de dinero por prestamo.',
        'Salud y Bienestar': 'Gastos que cuidan el cuerpo y la mente.',
        'Educación y Formación': 'Gastos para aprender o mejorar habilidades.',
        'Ropa': 'Gastos relacionados con prendas de vestir.',
        'Cuidado Personal': 'Bienestar físico e imagen.',
        'Compras': 'Bienes no recurrentes o de consumo duradero.',
        'Entretenimiento y Ocio': 'Actividades recreativas en casa o fuera.',
        'Viajes y Salidas': 'Escapadas o turismo.',
        'Bebidas Alcohólicas y Fiestas': 'Consumo de licor o fiestas sociales.',
        'Familia y Dependientes': 'Gastos asociados al núcleo familiar.',
        'Mascotas': 'Cuidado y bienestar animal.',
        'Regalos y Donaciones': 'Aportes emocionales o solidarios.',
        'Suscripciones y Membresías': 'Pagos recurrentes por servicios digitales.'
    }

    # Ejemplos predefinidos para las categorías
    DEFAULT_EXAMPLES = {
        'Vivienda': 'Arriendo, hipoteca, servicios públicos, mantenimiento',
        'Alimentación': 'Mercado, restaurantes, cafeterías, delivery',
        'Transporte y Movilidad': 'Transporte público, combustible, mantenimiento',
        'Préstamos': 'Tarjetas de crédito, préstamos, gota a gota',
        'Salud y Bienestar': 'Medicina, EPS, gimnasio, terapia',
        'Educación y Formación': 'Matrículas, cursos, libros',
        'Ropa': 'Ropa, calzado, accesorios',
        'Cuidado Personal': 'Peluquería, productos de higiene, estética',
        'Compras': 'Electrodomésticos, tecnología, hobbies',
        'Entretenimiento y Ocio': 'Streaming, cine, videojuegos',
        'Viajes y Salidas': 'Hoteles, pasajes, turismo',
        'Bebidas Alcohólicas y Fiestas': 'Bares, licores, celebraciones',
        'Familia y Dependientes': 'Cuidado infantil, educación hijos',
        'Mascotas': 'Veterinario, comida, juguetes',
        'Regalos y Donaciones': 'Regalos, ayudas, donaciones',
        'Suscripciones y Membresías': 'Netflix, Spotify, Amazon'
    }

    # Paleta de colores para nuevas categorías
    COLOR_PALETTE = [
        '#1E3A8A', '#10B981', '#F97316', '#DC2626', '#8B5CF6',
        '#FACC15', '#3B82F6', '#38BDF8', '#78350F', '#DB2777',
        '#FB923C', '#991B1B', '#166534', '#92400E', '#F472B6',
        '#0EA5E9', '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0',
        '#9966FF', '#FF9F40', '#8AC249', '#EA5545', '#F46A9B'
    ]

    name = models.CharField(max_length=100, unique=True)
    color = models.CharField(
        max_length=7, default='#CCCCCC')  # Formato hexadecimal
    description = models.TextField(blank=True, null=True)
    examples = models.TextField(blank=True, null=True)
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
    def get_default_description(cls, category_name):
        """Retorna la descripción por defecto para una categoría"""
        return cls.DEFAULT_DESCRIPTIONS.get(category_name, '')

    @classmethod
    def get_default_examples(cls, category_name):
        """Retorna los ejemplos por defecto para una categoría"""
        return cls.DEFAULT_EXAMPLES.get(category_name, '')

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
        """Sobrescribe el método save para asignar valores por defecto si no se proporcionan"""
        if not self.color or self.color == '#CCCCCC':
            # Primero intentar con el color predefinido
            default_color = self.get_default_color(self.name)
            if default_color != '#CCCCCC':
                self.color = default_color
            else:
                # Si no hay color predefinido, buscar uno no utilizado
                self.color = self.get_unused_color()

        # Asignar descripción por defecto si no se proporciona
        if not self.description:
            self.description = self.get_default_description(self.name)

        # Asignar ejemplos por defecto si no se proporcionan
        if not self.examples:
            self.examples = self.get_default_examples(self.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class UserExpenseCategory(models.Model):
    """Categorías de gastos personalizadas por usuario"""

    # Mantenemos las mismas categorías predefinidas que Category
    PREDEFINED_CATEGORIES = [
        'Vivienda', 'Alimentación', 'Transporte y Movilidad', 'Préstamos',
        'Salud y Bienestar', 'Educación y Formación', 'Ropa', 'Cuidado Personal',
        'Compras', 'Entretenimiento y Ocio', 'Viajes y Salidas',
        'Bebidas Alcohólicas y Fiestas', 'Familia y Dependientes', 'Mascotas',
        'Regalos y Donaciones', 'Suscripciones y Membresías'
    ]

    # Colores predefinidos para las categorías
    DEFAULT_COLORS = {
        'Vivienda': '#1E3A8A',
        'Alimentación': '#10B981',
        'Transporte y Movilidad': '#F97316',
        'Préstamos': '#DC2626',
        'Salud y Bienestar': '#8B5CF6',
        'Educación y Formación': '#FACC15',
        'Ropa': '#3B82F6',
        'Cuidado Personal': '#38BDF8',
        'Compras': '#78350F',
        'Entretenimiento y Ocio': '#DB2777',
        'Viajes y Salidas': '#FB923C',
        'Bebidas Alcohólicas y Fiestas': '#991B1B',
        'Familia y Dependientes': '#166534',
        'Mascotas': '#92400E',
        'Regalos y Donaciones': '#F472B6',
        'Suscripciones y Membresías': '#0EA5E9'
    }

    # Descripciones predefinidas para las categorías
    DEFAULT_DESCRIPTIONS = {
        'Vivienda': 'Gastos relacionados con el lugar donde se vive.',
        'Alimentación': 'Comida y bebidas no alcohólicas.',
        'Transporte y Movilidad': 'Gastos para moverse o mantener un vehículo.',
        'Préstamos': 'Salida de dinero por prestamo.',
        'Salud y Bienestar': 'Gastos que cuidan el cuerpo y la mente.',
        'Educación y Formación': 'Gastos para aprender o mejorar habilidades.',
        'Ropa': 'Gastos relacionados con prendas de vestir.',
        'Cuidado Personal': 'Bienestar físico e imagen.',
        'Compras': 'Bienes no recurrentes o de consumo duradero.',
        'Entretenimiento y Ocio': 'Actividades recreativas en casa o fuera.',
        'Viajes y Salidas': 'Escapadas o turismo.',
        'Bebidas Alcohólicas y Fiestas': 'Consumo de licor o fiestas sociales.',
        'Familia y Dependientes': 'Gastos asociados al núcleo familiar.',
        'Mascotas': 'Cuidado y bienestar animal.',
        'Regalos y Donaciones': 'Aportes emocionales o solidarios.',
        'Suscripciones y Membresías': 'Pagos recurrentes por servicios digitales.'
    }

    # Ejemplos predefinidos para las categorías
    DEFAULT_EXAMPLES = {
        'Vivienda': 'Arriendo, hipoteca, servicios públicos, mantenimiento',
        'Alimentación': 'Mercado, restaurantes, cafeterías, delivery',
        'Transporte y Movilidad': 'Transporte público, combustible, mantenimiento',
        'Préstamos': 'Tarjetas de crédito, préstamos, gota a gota',
        'Salud y Bienestar': 'Medicina, EPS, gimnasio, terapia',
        'Educación y Formación': 'Matrículas, cursos, libros',
        'Ropa': 'Ropa, calzado, accesorios',
        'Cuidado Personal': 'Peluquería, productos de higiene, estética',
        'Compras': 'Electrodomésticos, tecnología, hobbies',
        'Entretenimiento y Ocio': 'Streaming, cine, videojuegos',
        'Viajes y Salidas': 'Hoteles, pasajes, turismo',
        'Bebidas Alcohólicas y Fiestas': 'Bares, licores, celebraciones',
        'Familia y Dependientes': 'Cuidado infantil, educación hijos',
        'Mascotas': 'Veterinario, comida, juguetes',
        'Regalos y Donaciones': 'Regalos, ayudas, donaciones',
        'Suscripciones y Membresías': 'Netflix, Spotify, Amazon'
    }

    # Paleta de colores para nuevas categorías
    COLOR_PALETTE = [
        '#1E3A8A', '#10B981', '#F97316', '#DC2626', '#8B5CF6',
        '#FACC15', '#3B82F6', '#38BDF8', '#78350F', '#DB2777',
        '#FB923C', '#991B1B', '#166534', '#92400E', '#F472B6',
        '#0EA5E9', '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0',
        '#9966FF', '#FF9F40', '#8AC249', '#EA5545', '#F46A9B'
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='expense_categories')
    name = models.CharField(max_length=100)
    color = models.CharField(
        max_length=7, default='#CCCCCC')  # Formato hexadecimal
    description = models.TextField(blank=True, null=True)
    examples = models.TextField(blank=True, null=True)
    metadata = models.JSONField(blank=True, null=True)
    # Para marcar categorías predefinidas
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'name')  # Evitar duplicados por usuario
        ordering = ['name']

    @classmethod
    def get_default_color(cls, category_name):
        """Retorna el color por defecto para una categoría"""
        return cls.DEFAULT_COLORS.get(category_name, '#CCCCCC')

    @classmethod
    def get_default_description(cls, category_name):
        """Retorna la descripción por defecto para una categoría"""
        return cls.DEFAULT_DESCRIPTIONS.get(category_name, '')

    @classmethod
    def get_default_examples(cls, category_name):
        """Retorna los ejemplos por defecto para una categoría"""
        return cls.DEFAULT_EXAMPLES.get(category_name, '')

    def get_unused_color(self):
        """Retorna un color no utilizado de la paleta para este usuario"""
        used_colors = set(
            UserExpenseCategory.objects.filter(
                user=self.user).values_list('color', flat=True)
        )
        available_colors = [
            color for color in self.COLOR_PALETTE if color not in used_colors
        ]

        if available_colors:
            return random.choice(available_colors)

        # Si todos los colores están en uso, generar uno aleatorio
        return f'#{random.randint(0, 0xFFFFFF):06x}'

    def save(self, *args, **kwargs):
        """Sobrescribe el método save para asignar valores por defecto si no se proporcionan"""
        if not self.color or self.color == '#CCCCCC':
            # Primero intentar con el color predefinido
            default_color = self.get_default_color(self.name)
            if default_color != '#CCCCCC':
                self.color = default_color
            else:
                # Si no hay color predefinido, buscar uno no utilizado
                self.color = self.get_unused_color()

        # Asignar descripción por defecto si no se proporciona
        if not self.description:
            self.description = self.get_default_description(self.name)

        # Asignar ejemplos por defecto si no se proporcionan
        if not self.examples:
            self.examples = self.get_default_examples(self.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.name}"


class UserIncomeCategory(models.Model):
    """Categorías de ingresos personalizadas por usuario"""

    # Categorías predefinidas para ingresos
    PREDEFINED_CATEGORIES = [
        'Salario o Trabajo Fijo', 'Trabajo Independiente o Freelance', 'Negocios o Emprendimientos',
        'Inversiones', 'Alquileres y Activos', 'Regalías y Derechos',
        'Apoyos o Subsidios', 'Premios y Sorteos', 'Venta de Bienes'
    ]

    # Colores predefinidos para las categorías
    DEFAULT_COLORS = {
        'Salario o Trabajo Fijo': '#1E40AF',
        'Trabajo Independiente o Freelance': '#3B82F6',
        'Negocios o Emprendimientos': '#0F766E',
        'Inversiones': '#10B981',
        'Alquileres y Activos': '#78350F',
        'Regalías y Derechos': '#8B5CF6',
        'Apoyos o Subsidios': '#F59E0B',
        'Premios y Sorteos': '#DC2626',
        'Venta de Bienes': '#A16207'
    }

    # Descripciones predefinidas para las categorías
    DEFAULT_DESCRIPTIONS = {
        'Salario o Trabajo Fijo': 'Ingresos provenientes de empleos formales o contratos fijos.',
        'Trabajo Independiente o Freelance': 'Ingresos por trabajos por cuenta propia o por proyectos.',
        'Negocios o Emprendimientos': 'Ingresos generados por negocios propios.',
        'Inversiones': 'Rendimientos obtenidos por instrumentos financieros.',
        'Alquileres y Activos': 'Ingresos pasivos generados por propiedades o activos.',
        'Regalías y Derechos': 'Ingresos por propiedad intelectual o contenido.',
        'Apoyos o Subsidios': 'Ingresos no laborales provenientes de ayudas externas.',
        'Premios y Sorteos': 'Ingresos ocasionales por suerte o concursos.',
        'Venta de Bienes': 'Ingresos por venta de objetos personales o activos.'
    }

    # Ejemplos predefinidos para las categorías
    DEFAULT_EXAMPLES = {
        'Salario o Trabajo Fijo': 'Nómina, contratos laborales, trabajo a tiempo completo',
        'Trabajo Independiente o Freelance': 'Servicios profesionales, diseño, asesorías, honorarios',
        'Negocios o Emprendimientos': 'Ventas, e-commerce, microempresa',
        'Inversiones': 'Dividendos, acciones, cripto, rendimientos bancarios',
        'Alquileres y Activos': 'Arriendos, leasing, renting',
        'Regalías y Derechos': 'Regalías por música, libros, patentes',
        'Apoyos o Subsidios': 'Subsidios estatales, becas, ayudas familiares',
        'Premios y Sorteos': 'Loterías, rifas, juegos de azar',
        'Venta de Bienes': 'Venta de carro, muebles, tecnología'
    }

    # Paleta de colores para nuevas categorías
    COLOR_PALETTE = [
        '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF',
        '#FF9F40', '#8AC249', '#EA5545', '#F46A9B', '#EF9B20',
        '#EDBF33', '#87BC45', '#27AEEF', '#B33DC6', '#FF6384'
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='income_categories')
    name = models.CharField(max_length=100)
    color = models.CharField(
        max_length=7, default='#CCCCCC')  # Formato hexadecimal
    description = models.TextField(blank=True, null=True)
    example = models.TextField(blank=True, null=True)
    metadata = models.JSONField(blank=True, null=True)
    # Para marcar categorías predefinidas
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'name')  # Evitar duplicados por usuario
        ordering = ['name']

    @classmethod
    def get_default_color(cls, category_name):
        """Retorna el color por defecto para una categoría"""
        return cls.DEFAULT_COLORS.get(category_name, '#CCCCCC')

    @classmethod
    def get_default_description(cls, category_name):
        """Retorna la descripción por defecto para una categoría"""
        return cls.DEFAULT_DESCRIPTIONS.get(category_name, '')

    @classmethod
    def get_default_example(cls, category_name):
        """Retorna el ejemplo por defecto para una categoría"""
        return cls.DEFAULT_EXAMPLES.get(category_name, '')

    def get_unused_color(self):
        """Retorna un color no utilizado de la paleta para este usuario"""
        used_colors = set(
            UserIncomeCategory.objects.filter(
                user=self.user).values_list('color', flat=True)
        )
        available_colors = [
            color for color in self.COLOR_PALETTE if color not in used_colors
        ]

        if available_colors:
            return random.choice(available_colors)

        # Si todos los colores están en uso, generar uno aleatorio
        return f'#{random.randint(0, 0xFFFFFF):06x}'

    def save(self, *args, **kwargs):
        """Sobrescribe el método save para asignar valores por defecto si no se proporcionan"""
        if not self.color or self.color == '#CCCCCC':
            # Primero intentar con el color predefinido
            default_color = self.get_default_color(self.name)
            if default_color != '#CCCCCC':
                self.color = default_color
            else:
                # Si no hay color predefinido, buscar uno no utilizado
                self.color = self.get_unused_color()

        if not self.description:
            self.description = self.get_default_description(self.name)

        if not self.example:
            self.example = self.get_default_example(self.name)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.name}"
