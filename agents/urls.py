from django.urls import path

from .views import RiskAssessmentListView, RiskProfileView

urlpatterns = [
    path("risk-profile/", RiskProfileView.as_view(), name="agents-risk-profile"),
    path(
        "risk-profile/history/",
        RiskAssessmentListView.as_view(),
        name="agents-risk-profile-history",
    ),
]
