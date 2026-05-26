"""URL patterns for generic Composio integration endpoints.

Routes use a ``<str:toolkit>`` segment so the same view set handles
Gmail today and any other toolkit (Slack, Notion, banking) tomorrow.
The view dispatches to the matching handler via the registry.
"""

from __future__ import annotations

from django.urls import path

from . import views

app_name = "composio_integration"

urlpatterns = [
    path(
        "api/integrations/<str:toolkit>/connect-url/",
        views.ConnectUrlView.as_view(),
        name="connect-url",
    ),
    path(
        "api/integrations/<str:toolkit>/callback/",
        views.CallbackView.as_view(),
        name="callback",
    ),
    path(
        "api/integrations/<str:toolkit>/composio-webhook/",
        views.WebhookView.as_view(),
        name="webhook",
    ),
    path(
        "api/integrations/<str:toolkit>/disconnect/",
        views.DisconnectView.as_view(),
        name="disconnect",
    ),
    path(
        "api/integrations/<str:toolkit>/retry-trigger/",
        views.RetryTriggerView.as_view(),
        name="retry-trigger",
    ),
    path(
        "api/integrations/<str:toolkit>/status/",
        views.StatusView.as_view(),
        name="status",
    ),
]
