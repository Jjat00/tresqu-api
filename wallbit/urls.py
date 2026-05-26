from django.urls import path

from .views import (
    AgentConfirmView,
    AgentDecisionListView,
    AgentLimitsView,
    InvestmentListView,
    PortfolioHoldingsView,
    PortfolioSummaryView,
    PortfolioTimelineView,
    WallbitConnectView,
    WallbitDisconnectView,
    WallbitPauseView,
    WallbitResumeView,
    WallbitStatusView,
    WallbitSyncView,
)

urlpatterns = [
    path("connect/", WallbitConnectView.as_view(), name="wallbit-connect"),
    path("disconnect/", WallbitDisconnectView.as_view(), name="wallbit-disconnect"),
    path("pause/", WallbitPauseView.as_view(), name="wallbit-pause"),
    path("resume/", WallbitResumeView.as_view(), name="wallbit-resume"),
    path("status/", WallbitStatusView.as_view(), name="wallbit-status"),
    path("sync/", WallbitSyncView.as_view(), name="wallbit-sync"),
    path("agent/decisions/", AgentDecisionListView.as_view(), name="wallbit-agent-decisions"),
    path(
        "agent/confirm/<int:decision_id>/",
        AgentConfirmView.as_view(),
        name="wallbit-agent-confirm",
    ),
    path("limits/", AgentLimitsView.as_view(), name="wallbit-limits"),
    path("investments/", InvestmentListView.as_view(), name="wallbit-investments"),
    path("portfolio/summary/", PortfolioSummaryView.as_view(), name="wallbit-portfolio-summary"),
    path("portfolio/holdings/", PortfolioHoldingsView.as_view(), name="wallbit-portfolio-holdings"),
    path("portfolio/timeline/", PortfolioTimelineView.as_view(), name="wallbit-portfolio-timeline"),
]
