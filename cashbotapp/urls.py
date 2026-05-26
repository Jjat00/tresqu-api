"""
URL configuration for cashbotapp project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('expenses.urls'), name='expenses'),
    path('api/', include('users.urls'), name='users'),
    path('api/', include('income.urls'), name='income'),
    path('api/categories/', include('categories.urls'), name='categories'),
    path('api/savings/', include('savings.urls'), name='savings'),
    path('telegram/', include('telegrambot.urls'), name='telegram'),
    path('whatsapp/', include('whatsappbot.urls'), name='whatsapp'),
    path('api/gmail/', include('gmailbot.urls'), name='gmail'),
    path('api/wallbit/', include('wallbit.urls'), name='wallbit'),
    path('api/agents/', include('agents.urls'), name='agents'),
    path('', include('composio_integration.urls'), name='composio-integration'),
    # JWT auth
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    # API Documentation
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('schema/swagger-ui/',
         SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('schema/redoc/',
         SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]
