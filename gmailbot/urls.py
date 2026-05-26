from django.urls import path

from . import views

urlpatterns = [
    path(
        'processed-emails/',
        views.ProcessedEmailListView.as_view(),
        name='gmail-processed-emails',
    ),
]
