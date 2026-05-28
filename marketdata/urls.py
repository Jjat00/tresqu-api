from django.urls import path

from .views import AssetPriceHistoryView

urlpatterns = [
    path(
        "assets/<str:symbol>/history/",
        AssetPriceHistoryView.as_view(),
        name="market-asset-history",
    ),
]
