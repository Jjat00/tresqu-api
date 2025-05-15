from django.urls import path
from . import views

app_name = 'whatsappbot'

urlpatterns = [
    # Ruta para recibir eventos de webhook desde la API de WhatsApp
    path('webhook/<str:instance_name>/',
         views.webhook_receiver, name='webhook_receiver'),
]
