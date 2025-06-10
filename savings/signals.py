from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from .management.commands.create_user_savings_categories import create_default_categories_for_user
from .management.commands.create_user_savings_templates import create_default_templates_for_user
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_user_savings_setup(sender, instance, created, **kwargs):
    """
    Señal que se ejecuta después de crear un usuario para configurar 
    automáticamente sus categorías y plantillas de ahorro predefinidas.
    """
    if created:  # Solo para usuarios recién creados
        try:
            # Crear categorías predefinidas
            categories_created = create_default_categories_for_user(instance)

            if categories_created:
                logger.info(
                    f'Categorías de ahorro creadas para el usuario {instance.username}')

                # Crear plantillas predefinidas (requiere que existan categorías)
                templates_created = create_default_templates_for_user(instance)

                if templates_created:
                    logger.info(
                        f'Plantillas de ahorro creadas para el usuario {instance.username}')
                else:
                    logger.warning(
                        f'No se pudieron crear plantillas para el usuario {instance.username}')
            else:
                logger.warning(
                    f'No se pudieron crear categorías para el usuario {instance.username}')

        except Exception as e:
            logger.error(
                f'Error al configurar ahorro para el usuario {instance.username}: {str(e)}')


@receiver(post_save, sender=User)
def log_user_creation(sender, instance, created, **kwargs):
    """
    Señal adicional para registrar la creación de usuarios (debug)
    """
    if created:
        logger.info(
            f'Nuevo usuario creado: {instance.username} (ID: {instance.id})')
