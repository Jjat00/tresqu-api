from django.urls import path
from . import views

app_name = 'whatsappbot'

urlpatterns = [
    # Ruta principal para webhook de Meta WhatsApp API (verificación y eventos)
    path('webhook/', views.meta_webhook, name='meta_webhook'),

    # Rutas para autenticación de usuarios mediante códigos
    path('send-code/',
         views.send_verification_code_meta, name='send_verification_code'),
    path('verify-code/',
         views.verify_code, name='verify_code'),
]
