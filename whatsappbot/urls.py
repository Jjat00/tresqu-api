from django.urls import path
from . import views

app_name = 'whatsappbot'

urlpatterns = [
    # Ruta para recibir eventos de webhook desde la API de WhatsApp
    path('webhook/<str:instance_name>/',
         views.webhook_receiver, name='webhook_receiver'),
    # Rutas para autenticación de usuarios mediante códigos
    path('send-code/<str:instance_name>/',
         views.send_verification_code, name='send_verification_code'),
    path('verify-code/',
         views.verify_code, name='verify_code'),
]
