from django.apps import AppConfig


class SavingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'savings'

    def ready(self):
        import savings.signals
    verbose_name = 'Módulo de Ahorros'

    def ready(self):
        # Importar señales si las hay en el futuro
        pass
