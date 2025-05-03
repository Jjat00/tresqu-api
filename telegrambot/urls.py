from django.urls import path
from . import views

urlpatterns = [
    path('webhook/', views.telegram_webhook, name='telegram_webhook'),
    path('set-webhook/', views.set_webhook, name='set_webhook'),
    path('debug/', views.env_debug, name='env_debug'),
]
