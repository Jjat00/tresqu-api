from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import User


@receiver(post_save, sender=User)
def assign_trial_subscription(sender, instance, created, **kwargs):
    """
    Asigna automáticamente el plan premium de prueba a los nuevos usuarios
    """
    if created:
        # Solo asignar el plan de prueba si es un usuario nuevo
        instance.assign_premium_trial()
