from django.core.management.base import BaseCommand
from decimal import Decimal
from savings.models import SavingsCategory, SavingsTemplate


class Command(BaseCommand):
    help = 'Crea categorías y plantillas de ahorro iniciales'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(
            'Creando categorías de ahorro...'))

        # Crear categorías de ahorro
        categories_data = [
            {
                'name': 'Fondo de Emergencia',
                'description': 'Reserva para emergencias y gastos inesperados',
                'color': '#FF6B6B',  # Rojo
                'icon': 'emergency'
            },
            {
                'name': 'Vacaciones',
                'description': 'Ahorro para viajes y vacaciones',
                'color': '#4ECDC4',  # Turquesa
                'icon': 'travel'
            },
            {
                'name': 'Compra de Casa',
                'description': 'Ahorro para la compra de vivienda',
                'color': '#45B7D1',  # Azul
                'icon': 'home'
            },
            {
                'name': 'Compra de Vehículo',
                'description': 'Ahorro para la compra de automóvil o moto',
                'color': '#96CEB4',  # Verde claro
                'icon': 'car'
            },
            {
                'name': 'Educación',
                'description': 'Ahorro para estudios, cursos y capacitaciones',
                'color': '#FFEAA7',  # Amarillo
                'icon': 'education'
            },
            {
                'name': 'Jubilación',
                'description': 'Ahorro para el retiro y pensión',
                'color': '#DDA0DD',  # Lila
                'icon': 'retirement'
            },
            {
                'name': 'Inversiones',
                'description': 'Capital para inversiones y negocios',
                'color': '#74B9FF',  # Azul claro
                'icon': 'investment'
            },
            {
                'name': 'Salud',
                'description': 'Ahorro para gastos médicos y de salud',
                'color': '#55A3FF',  # Azul médico
                'icon': 'health'
            },
            {
                'name': 'Tecnología',
                'description': 'Ahorro para equipos tecnológicos y gadgets',
                'color': '#636E72',  # Gris
                'icon': 'tech'
            },
            {
                'name': 'Eventos Especiales',
                'description': 'Bodas, cumpleaños, celebraciones',
                'color': '#FD79A8',  # Rosa
                'icon': 'celebration'
            }
        ]

        categories = {}
        for cat_data in categories_data:
            category, created = SavingsCategory.objects.get_or_create(
                name=cat_data['name'],
                defaults=cat_data
            )
            categories[cat_data['name']] = category
            if created:
                self.stdout.write(f'✓ Categoría creada: {cat_data["name"]}')
            else:
                self.stdout.write(f'- Categoría ya existe: {cat_data["name"]}')

        self.stdout.write(self.style.SUCCESS(
            '\nCreando plantillas de ahorro...'))

        # Crear plantillas de ahorro
        templates_data = [
            {
                'name': 'Fondo de Emergencia Básico',
                'description': 'Reserva para cubrir 3-6 meses de gastos básicos',
                'category': categories['Fondo de Emergencia'],
                'suggested_amount_min': Decimal('1000000'),  # 1M COP
                'suggested_amount_max': Decimal('5000000'),  # 5M COP
                'suggested_timeframe_months': 12,
                'priority': 'urgent',
                'tips': 'Prioriza este fondo antes que cualquier otro ahorro. Debe ser tu primera meta financiera.'
            },
            {
                'name': 'Vacaciones Familiares',
                'description': 'Ahorro para vacaciones anuales en familia',
                'category': categories['Vacaciones'],
                'suggested_amount_min': Decimal('2000000'),  # 2M COP
                'suggested_amount_max': Decimal('8000000'),  # 8M COP
                'suggested_timeframe_months': 12,
                'priority': 'medium',
                'tips': 'Planifica con un año de anticipación para obtener mejores precios en vuelos y hoteles.'
            },
            {
                'name': 'Cuota Inicial Vivienda',
                'description': 'Ahorro para el enganche de una casa o apartamento',
                'category': categories['Compra de Casa'],
                'suggested_amount_min': Decimal('20000000'),  # 20M COP
                'suggested_amount_max': Decimal('100000000'),  # 100M COP
                'suggested_timeframe_months': 60,
                'priority': 'high',
                'tips': 'Necesitarás aproximadamente el 30% del valor de la vivienda como cuota inicial.'
            },
            {
                'name': 'Vehículo Usado',
                'description': 'Ahorro para la compra de un automóvil usado',
                'category': categories['Compra de Vehículo'],
                'suggested_amount_min': Decimal('15000000'),  # 15M COP
                'suggested_amount_max': Decimal('40000000'),  # 40M COP
                'suggested_timeframe_months': 24,
                'priority': 'medium',
                'tips': 'Considera también los gastos adicionales como seguro, traspaso y mantenimiento.'
            },
            {
                'name': 'Curso de Especialización',
                'description': 'Ahorro para estudios de posgrado o certificaciones',
                'category': categories['Educación'],
                'suggested_amount_min': Decimal('3000000'),  # 3M COP
                'suggested_amount_max': Decimal('20000000'),  # 20M COP
                'suggested_timeframe_months': 18,
                'priority': 'high',
                'tips': 'La educación es una de las mejores inversiones a largo plazo.'
            },
            {
                'name': 'Fondo de Jubilación',
                'description': 'Ahorro complementario para la pensión',
                'category': categories['Jubilación'],
                'suggested_amount_min': Decimal('5000000'),  # 5M COP
                'suggested_amount_max': Decimal('50000000'),  # 50M COP
                'suggested_timeframe_months': 120,
                'priority': 'high',
                'tips': 'Comienza lo antes posible para aprovechar el interés compuesto.'
            },
            {
                'name': 'Capital de Inversión',
                'description': 'Dinero para invertir en acciones, bonos o negocios',
                'category': categories['Inversiones'],
                'suggested_amount_min': Decimal('2000000'),  # 2M COP
                'suggested_amount_max': Decimal('20000000'),  # 20M COP
                'suggested_timeframe_months': 12,
                'priority': 'medium',
                'tips': 'Solo invierte dinero que puedas permitirte perder. Diversifica tus inversiones.'
            },
            {
                'name': 'Seguro de Salud Privado',
                'description': 'Ahorro para pólizas de salud complementarias',
                'category': categories['Salud'],
                'suggested_amount_min': Decimal('1500000'),  # 1.5M COP
                'suggested_amount_max': Decimal('5000000'),  # 5M COP
                'suggested_timeframe_months': 6,
                'priority': 'high',
                'tips': 'La salud no tiene precio. Considera un seguro médico complementario.'
            },
            {
                'name': 'Equipos de Trabajo',
                'description': 'Laptop, computadora o equipos profesionales',
                'category': categories['Tecnología'],
                'suggested_amount_min': Decimal('1000000'),  # 1M COP
                'suggested_amount_max': Decimal('8000000'),  # 8M COP
                'suggested_timeframe_months': 8,
                'priority': 'medium',
                'tips': 'Invierte en tecnología que mejore tu productividad laboral.'
            },
            {
                'name': 'Boda',
                'description': 'Ahorro para ceremonia y celebración de matrimonio',
                'category': categories['Eventos Especiales'],
                'suggested_amount_min': Decimal('10000000'),  # 10M COP
                'suggested_amount_max': Decimal('50000000'),  # 50M COP
                'suggested_timeframe_months': 18,
                'priority': 'medium',
                'tips': 'Planifica con tiempo y establece un presupuesto realista. No te endeudes por una boda.'
            }
        ]

        for template_data in templates_data:
            template, created = SavingsTemplate.objects.get_or_create(
                name=template_data['name'],
                category=template_data['category'],
                defaults=template_data
            )
            if created:
                self.stdout.write(
                    f'✓ Plantilla creada: {template_data["name"]}')
            else:
                self.stdout.write(
                    f'- Plantilla ya existe: {template_data["name"]}')

        self.stdout.write(self.style.SUCCESS(
            '\n¡Datos iniciales creados exitosamente!'))
        self.stdout.write(self.style.WARNING(
            'Categorías creadas: {}'.format(len(categories_data))))
        self.stdout.write(self.style.WARNING(
            'Plantillas creadas: {}'.format(len(templates_data))))
