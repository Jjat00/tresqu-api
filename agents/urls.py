from django.urls import path

from .views import (
    AgentChatStreamView,
    RiskAssessmentListView,
    RiskProfileEffectiveView,
    RiskProfileView,
)

urlpatterns = [
    path("chat/stream/", AgentChatStreamView.as_view(), name="agents-chat-stream"),
    path("risk-profile/", RiskProfileView.as_view(), name="agents-risk-profile"),
    path(
        "risk-profile/effective/",
        RiskProfileEffectiveView.as_view(),
        name="agents-risk-profile-effective",
    ),
    path(
        "risk-profile/history/",
        RiskAssessmentListView.as_view(),
        name="agents-risk-profile-history",
    ),
]
