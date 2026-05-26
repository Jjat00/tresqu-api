"""Vistas residuales de ``gmailbot`` tras la migración a Composio.

El flujo OAuth + Pub/Sub directo (``GmailOAuthURLView``,
``GmailOAuthCallbackView``, ``GmailDisconnectView``, ``GmailStatusView``,
``GmailSyncView``, ``GmailWebhookView``) fue retirado. Su reemplazo vive
en ``composio_integration.views`` (``ConnectUrlView``, ``CallbackView``,
``DisconnectView``, ``WebhookView``).

Solo conservamos ``ProcessedEmailListView`` porque el dashboard del
frontend la consume para mostrar el historial de emails procesados.
"""

from __future__ import annotations

import logging

from rest_framework.generics import ListAPIView

from .models import ProcessedEmail
from .serializers import ProcessedEmailSerializer

logger = logging.getLogger(__name__)


class ProcessedEmailListView(ListAPIView):
    """Lista paginada de emails procesados del usuario autenticado.

    El FK durable de ``ProcessedEmail`` es ``user`` (ver migración 0004);
    la conexión Composio asociada es informativa y puede ser ``NULL`` si
    la conexión fue desconectada.
    """

    serializer_class = ProcessedEmailSerializer

    def get_queryset(self):
        user = self.request.user
        return (
            ProcessedEmail.objects.filter(user=user)
            .select_related('expense', 'expense__user_expense_category')
            .all()
        )
