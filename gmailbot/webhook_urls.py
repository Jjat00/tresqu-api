from django.urls import path
from . import views

urlpatterns = [
    path('webhook/', views.GmailWebhookView.as_view(), name='gmail-webhook'),
]
