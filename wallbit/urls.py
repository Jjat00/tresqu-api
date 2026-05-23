from django.urls import path

from .views import (
    AgentConfirmView,
    AgentDecisionListView,
    AgentLimitsView,
    WallbitConnectView,
    WallbitDisconnectView,
    WallbitStatusView,
    WallbitSyncView,
)

urlpatterns = [
    path("connect/", WallbitConnectView.as_view(), name="wallbit-connect"),
    path("disconnect/", WallbitDisconnectView.as_view(), name="wallbit-disconnect"),
    path("status/", WallbitStatusView.as_view(), name="wallbit-status"),
    path("sync/", WallbitSyncView.as_view(), name="wallbit-sync"),
    path("agent/decisions/", AgentDecisionListView.as_view(), name="wallbit-agent-decisions"),
    path(
        "agent/confirm/<int:decision_id>/",
        AgentConfirmView.as_view(),
        name="wallbit-agent-confirm",
    ),
    path("limits/", AgentLimitsView.as_view(), name="wallbit-limits"),
]
