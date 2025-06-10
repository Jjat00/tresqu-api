from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from decimal import Decimal
from savings.models import SavingsCategory, SavingsTemplate


class Command(BaseCommand):
    help = 'Crea categorías de ahorro predefinidas para un usuario específico'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='ID del usuario para el cual crear las categorías',
        )
        parser.add_argument(
            '--username',
            type=str,
            help='Username del usuario para el cual crear las categorías',
        )
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='Crear categorías para todos los usuarios que no las tengan',
        )

    def handle(self, *args, **options):
        if options['all_users']:
            self.create_categories_for_all_users()
        elif options['user_id']:
            try:
                user = User.objects.get(id=options['user_id'])
                self.create_categories_for_user(user)
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f'Usuario con ID {options["user_id"]} no encontrado'))
        elif options['username']:
            try:
                user = User.objects.get(username=options['username'])
                self.create_categories_for_user(user)
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(
                    f'Usuario "{options["username"]}" no encontrado'))
        else:
            self.stdout.write(self.style.ERROR(
                'Debes especificar --user-id, --username o --all-users'))

    def create_categories_for_all_users(self):
        """Crear categorías para todos los usuarios que no las tengan"""
        users_without_categories = User.objects.exclude(
            savingscategory__isnull=False
        ).distinct()

        self.stdout.write(
            f'Encontrados {users_without_categories.count()} usuarios sin categorías de ahorro')

        for user in users_without_categories:
            self.create_categories_for_user(
                user, show_individual_messages=False)

        self.stdout.write(self.style.SUCCESS(
            f'Categorías creadas para {users_without_categories.count()} usuarios'))

    def create_categories_for_user(self, user, show_individual_messages=True):
        """Crear categorías predefinidas para un usuario específico"""
        if show_individual_messages:
            self.stdout.write(self.style.SUCCESS(
                f'Creando categorías de ahorro para {user.username}...'))

        # Verificar si ya tiene categorías
        existing_categories = SavingsCategory.objects.filter(user=user).count()
        if existing_categories > 0:
            if show_individual_messages:
                self.stdout.write(self.style.WARNING(
                    f'El usuario {user.username} ya tiene {existing_categories} categorías'))
            return

        # Datos de categorías predefinidas
        categories_data = [
            {
                'name': 'Fondo de Emergencia',
                'description': 'Reserva para emergencias y gastos inesperados',
                'color': '#FF6B6B',  # Rojo
                'icon': 'emergency',
                'is_default': True
            },
            {
                'name': 'Vacaciones',
                'description': 'Ahorro para viajes y vacaciones',
                'color': '#4ECDC4',  # Turquesa
                'icon': 'travel',
                'is_default': True
            },
            {
                'name': 'Compra de Casa',
                'description': 'Ahorro para la compra de vivienda',
                'color': '#45B7D1',  # Azul
                'icon': 'home',
                'is_default': True
            },
            {
                'name': 'Compra de Vehículo',
                'description': 'Ahorro para la compra de automóvil o moto',
                'color': '#96CEB4',  # Verde claro
                'icon': 'car',
                'is_default': True
            },
            {
                'name': 'Educación',
                'description': 'Ahorro para estudios, cursos y capacitaciones',
                'color': '#FFEAA7',  # Amarillo
                'icon': 'education',
                'is_default': True
            },
            {
                'name': 'Jubilación',
                'description': 'Ahorro para el retiro y pensión',
                'color': '#DDA0DD',  # Lila
                'icon': 'retirement',
                'is_default': True
            },
            {
                'name': 'Inversiones',
                'description': 'Capital para inversiones y negocios',
                'color': '#74B9FF',  # Azul claro
                'icon': 'investment',
                'is_default': True
            },
            {
                'name': 'Salud',
                'description': 'Ahorro para gastos médicos y de salud',
                'color': '#55A3FF',  # Azul médico
                'icon': 'health',
                'is_default': True
            },
            {
                'name': 'Tecnología',
                'description': 'Ahorro para equipos tecnológicos y gadgets',
                'color': '#636E72',  # Gris
                'icon': 'tech',
                'is_default': True
            },
            {
                'name': 'Eventos Especiales',
                'description': 'Bodas, cumpleaños, celebraciones',
                'color': '#FD79A8',  # Rosa
                'icon': 'celebration',
                'is_default': True
            }
        ]

        categories_created = 0
        for cat_data in categories_data:
            category, created = SavingsCategory.objects.get_or_create(
                user=user,
                name=cat_data['name'],
                defaults=cat_data
            )
            if created:
                categories_created += 1
                if show_individual_messages:
                    self.stdout.write(
                        f'✓ Categoría creada: {cat_data["name"]}')

        if show_individual_messages:
            self.stdout.write(self.style.SUCCESS(
                f'¡Categorías creadas exitosamente para {user.username}!'))
            self.stdout.write(self.style.WARNING(
                f'Total de categorías creadas: {categories_created}'))


def create_default_categories_for_user(user):
    """
    Función utilitaria para crear categorías predefinidas para un usuario.
    Puede ser llamada desde señales de registro de usuario.
    """
    # Verificar si ya tiene categorías
    if SavingsCategory.objects.filter(user=user).exists():
        return False

    # Datos de categorías predefinidas
    categories_data = [
        {
            'name': 'Fondo de Emergencia',
            'description': 'Reserva para emergencias y gastos inesperados',
            'color': '#FF6B6B',
            'icon': 'emergency',
            'is_default': True
        },
        {
            'name': 'Vacaciones',
            'description': 'Ahorro para viajes y vacaciones',
            'color': '#4ECDC4',
            'icon': 'travel',
            'is_default': True
        },
        {
            'name': 'Compra de Casa',
            'description': 'Ahorro para la compra de vivienda',
            'color': '#45B7D1',
            'icon': 'home',
            'is_default': True
        },
        {
            'name': 'Compra de Vehículo',
            'description': 'Ahorro para la compra de automóvil o moto',
            'color': '#96CEB4',
            'icon': 'car',
            'is_default': True
        },
        {
            'name': 'Educación',
            'description': 'Ahorro para estudios, cursos y capacitaciones',
            'color': '#FFEAA7',
            'icon': 'education',
            'is_default': True
        },
        {
            'name': 'Jubilación',
            'description': 'Ahorro para el retiro y pensión',
            'color': '#DDA0DD',
            'icon': 'retirement',
            'is_default': True
        },
        {
            'name': 'Inversiones',
            'description': 'Capital para inversiones y negocios',
            'color': '#74B9FF',
            'icon': 'investment',
            'is_default': True
        },
        {
            'name': 'Salud',
            'description': 'Ahorro para gastos médicos y de salud',
            'color': '#55A3FF',
            'icon': 'health',
            'is_default': True
        },
        {
            'name': 'Tecnología',
            'description': 'Ahorro para equipos tecnológicos y gadgets',
            'color': '#636E72',
            'icon': 'tech',
            'is_default': True
        },
        {
            'name': 'Eventos Especiales',
            'description': 'Bodas, cumpleaños, celebraciones',
            'color': '#FD79A8',
            'icon': 'celebration',
            'is_default': True
        }
    ]

    # Crear categorías
    categories_created = 0
    for cat_data in categories_data:
        category, created = SavingsCategory.objects.get_or_create(
            user=user,
            name=cat_data['name'],
            defaults=cat_data
        )
        if created:
            categories_created += 1

    return categories_created > 0
