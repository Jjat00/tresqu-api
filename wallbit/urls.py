from django.urls import path

from .views import WallbitConnectView, WallbitDisconnectView, WallbitStatusView

urlpatterns = [
    path("connect", WallbitConnectView.as_view(), name="wallbit-connect"),
    path("disconnect", WallbitDisconnectView.as_view(), name="wallbit-disconnect"),
    path("status", WallbitStatusView.as_view(), name="wallbit-status"),
]
