from django.urls import path

from .views import AssetPriceHistoryView, SparklinesView

urlpatterns = [
    path(
        "assets/<str:symbol>/history/",
        AssetPriceHistoryView.as_view(),
        name="market-asset-history",
    ),
    path("sparklines/", SparklinesView.as_view(), name="market-sparklines"),
]
