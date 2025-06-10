from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from decimal import Decimal
from savings.models import SavingsCategory, SavingsTemplate


class Command(BaseCommand):
    help = 'Crea plantillas de ahorro predefinidas para un usuario específico basadas en sus categorías'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='ID del usuario para el cual crear las plantillas',
        )
        parser.add_argument(
            '--username',
            type=str,
            help='Username del usuario para el cual crear las plantillas',
        )
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='Crear plantillas para todos los usuarios que tengan categorías',
        )

    def handle(self, *args, **options):
        if options['all_users']:
            self.create_templates_for_all_users()
        elif options['user_id']:
            try:
                user = User.objects.get(id=options['user_id'])
                self.create_templates_for_user(user)
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f'Usuario con ID {options["user_id"]} no encontrado'))
        elif options['username']:
            try:
                user = User.objects.get(username=options['username'])
                self.create_templates_for_user(user)
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f'Usuario "{options["username"]}" no encontrado'))
        else:
            self.stdout.write(self.style.ERROR(
                'Debes especificar --user-id, --username o --all-users'))

    def create_templates_for_all_users(self):
        """Crear plantillas para todos los usuarios que tengan categorías"""
        users_with_categories = User.objects.filter(
            savingscategory__isnull=False
        ).distinct()

        self.stdout.write(
            f'Encontrados {users_with_categories.count()} usuarios con categorías de ahorro')

        for user in users_with_categories:
            self.create_templates_for_user(
                user, show_individual_messages=False)

        self.stdout.write(self.style.SUCCESS(
            f'Plantillas creadas para {users_with_categories.count()} usuarios'))

    def create_templates_for_user(self, user, show_individual_messages=True):
        """Crear plantillas predefinidas para un usuario específico basadas en sus categorías"""
        if show_individual_messages:
            self.stdout.write(self.style.SUCCESS(
                f'Creando plantillas de ahorro para {user.username}...'))

        # Obtener categorías del usuario
        user_categories = SavingsCategory.objects.filter(
            user=user, is_active=True)
        if not user_categories.exists():
            if show_individual_messages:
                self.stdout.write(self.style.WARNING(
                    f'El usuario {user.username} no tiene categorías de ahorro'))
            return

        # Crear diccionario de categorías por nombre para fácil búsqueda
        categories_dict = {cat.name: cat for cat in user_categories}

        # Plantillas predefinidas con categorías
        templates_data = [
            {
                'name': 'Fondo de Emergencia Básico',
                'description': 'Fondo de emergencia para cubrir 3-6 meses de gastos básicos',
                'category_name': 'Fondo de Emergencia',
                'suggested_amount_min': Decimal('3000000'),  # 3M COP
                'suggested_amount_max': Decimal('10000000'),  # 10M COP
                'suggested_timeframe_months': 12,
                'priority': 'urgent',
                'tips': 'Prioriza este fondo antes que cualquier otro ahorro. Debe cubrir al menos 6 meses de gastos esenciales.'
            },
            {
                'name': 'Vacaciones Familiares',
                'description': 'Ahorro para vacaciones familiares anuales dentro del país',
                'category_name': 'Vacaciones',
                'suggested_amount_min': Decimal('2000000'),  # 2M COP
                'suggested_amount_max': Decimal('8000000'),  # 8M COP
                'suggested_timeframe_months': 12,
                'priority': 'medium',
                'tips': 'Planifica con anticipación y busca ofertas. Considera destinos nacionales para reducir costos.'
            },
            {
                'name': 'Cuota Inicial de Casa',
                'description': 'Ahorro para la cuota inicial de vivienda (20-30% del valor)',
                'category_name': 'Compra de Casa',
                'suggested_amount_min': Decimal('30000000'),  # 30M COP
                'suggested_amount_max': Decimal('100000000'),  # 100M COP
                'suggested_timeframe_months': 60,
                'priority': 'high',
                'tips': 'Ahorra al menos el 20% del valor de la vivienda. Considera subsidios gubernamentales disponibles.'
            },
            {
                'name': 'Vehículo Usado',
                'description': 'Ahorro para la compra de un vehículo usado en buen estado',
                'category_name': 'Compra de Vehículo',
                'suggested_amount_min': Decimal('15000000'),  # 15M COP
                'suggested_amount_max': Decimal('40000000'),  # 40M COP
                'suggested_timeframe_months': 24,
                'priority': 'medium',
                'tips': 'Considera el costo total de propiedad incluyendo seguros, mantenimiento y combustible.'
            },
            {
                'name': 'Curso de Especialización',
                'description': 'Ahorro para cursos, certificaciones o estudios de posgrado',
                'category_name': 'Educación',
                'suggested_amount_min': Decimal('3000000'),  # 3M COP
                'suggested_amount_max': Decimal('15000000'),  # 15M COP
                'suggested_timeframe_months': 18,
                'priority': 'high',
                'tips': 'Investiga el retorno de inversión del curso. Busca becas o financiamiento disponible.'
            },
            {
                'name': 'Fondo de Pensiones Voluntarias',
                'description': 'Ahorro adicional para el retiro a través de fondos voluntarios',
                'category_name': 'Jubilación',
                'suggested_amount_min': Decimal('5000000'),  # 5M COP
                'suggested_amount_max': Decimal('50000000'),  # 50M COP
                'suggested_timeframe_months': 120,
                'priority': 'high',
                'tips': 'Aprovecha los beneficios tributarios. Comienza temprano para maximizar el interés compuesto.'
            },
            {
                'name': 'Capital de Inversión',
                'description': 'Fondo para inversiones en acciones, bonos o fondos mutuos',
                'category_name': 'Inversiones',
                'suggested_amount_min': Decimal('2000000'),  # 2M COP
                'suggested_amount_max': Decimal('20000000'),  # 20M COP
                'suggested_timeframe_months': 24,
                'priority': 'medium',
                'tips': 'Solo invierte dinero que puedas permitirte perder. Diversifica tu portafolio.'
            },
            {
                'name': 'Seguro Médico Privado',
                'description': 'Ahorro para pólizas de salud complementarias',
                'category_name': 'Salud',
                'suggested_amount_min': Decimal('1000000'),  # 1M COP
                'suggested_amount_max': Decimal('5000000'),  # 5M COP
                'suggested_timeframe_months': 12,
                'priority': 'high',
                'tips': 'Considera tu historial médico familiar. Compara diferentes aseguradoras antes de decidir.'
            },
            {
                'name': 'Equipo de Trabajo',
                'description': 'Ahorro para computadora, software y herramientas profesionales',
                'category_name': 'Tecnología',
                'suggested_amount_min': Decimal('3000000'),  # 3M COP
                'suggested_amount_max': Decimal('12000000'),  # 12M COP
                'suggested_timeframe_months': 18,
                'priority': 'medium',
                'tips': 'Evalúa tus necesidades reales. Considera equipos reacondicionados para ahorrar dinero.'
            },
            {
                'name': 'Boda',
                'description': 'Ahorro para ceremonia y celebración de boda',
                'category_name': 'Eventos Especiales',
                'suggested_amount_min': Decimal('10000000'),  # 10M COP
                'suggested_amount_max': Decimal('50000000'),  # 50M COP
                'suggested_timeframe_months': 24,
                'priority': 'low',
                'tips': 'Define un presupuesto realista. Prioriza lo que realmente importa para ustedes como pareja.'
            }
        ]

        templates_created = 0
        for template_data in templates_data:
            category_name = template_data.pop('category_name')

            # Verificar si el usuario tiene esta categoría
            if category_name not in categories_dict:
                if show_individual_messages:
                    self.stdout.write(
                        f'⚠️  Categoría "{category_name}" no encontrada para {user.username}')
                continue

            # Asignar la categoría del usuario
            template_data['category'] = categories_dict[category_name]

            # Verificar si ya existe una plantilla con este nombre para esta categoría
            existing_template = SavingsTemplate.objects.filter(
                category=categories_dict[category_name],
                name=template_data['name']
            ).first()

            if existing_template:
                if show_individual_messages:
                    self.stdout.write(
                        f'⚠️  Plantilla "{template_data["name"]}" ya existe para {user.username}')
                continue

            # Crear la plantilla
            template = SavingsTemplate.objects.create(**template_data)
            templates_created += 1

            if show_individual_messages:
                self.stdout.write(f'✓ Plantilla creada: {template.name}')

        if show_individual_messages:
            self.stdout.write(self.style.SUCCESS(
                f'¡Plantillas creadas exitosamente para {user.username}!'))
            self.stdout.write(self.style.WARNING(
                f'Total de plantillas creadas: {templates_created}'))


def create_default_templates_for_user(user):
    """
    Función utilitaria para crear plantillas predefinidas para un usuario.
    Puede ser llamada desde señales de registro de usuario.
    """
    # Verificar si el usuario tiene categorías
    user_categories = SavingsCategory.objects.filter(user=user, is_active=True)
    if not user_categories.exists():
        return False

    # Usar el comando para crear las plantillas
    command = Command()
    command.create_templates_for_user(user, show_individual_messages=False)
    return True
