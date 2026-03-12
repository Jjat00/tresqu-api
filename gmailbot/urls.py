from django.urls import path
from . import views

urlpatterns = [
    path('oauth/url/', views.GmailOAuthURLView.as_view(), name='gmail-oauth-url'),
    path('oauth/callback/', views.GmailOAuthCallbackView.as_view(), name='gmail-oauth-callback'),
    path('disconnect/', views.GmailDisconnectView.as_view(), name='gmail-disconnect'),
    path('status/', views.GmailStatusView.as_view(), name='gmail-status'),
    path('processed-emails/', views.ProcessedEmailListView.as_view(), name='gmail-processed-emails'),
    path('sync/', views.GmailSyncView.as_view(), name='gmail-sync'),
]
