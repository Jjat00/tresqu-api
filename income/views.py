from django.shortcuts import render
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum
from datetime import datetime, timedelta
from django.utils import timezone

from .models import Income, IncomeCategory
from .serializers import IncomeSerializer, IncomeCategorySerializer


class IncomeCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = IncomeCategorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return IncomeCategory.objects.all()


class IncomeViewSet(viewsets.ModelViewSet):
    serializer_class = IncomeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return Income.objects.filter(user=user).order_by('-timestamp')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Obtiene un resumen de ingresos por período"""
        user = request.user
        period = request.query_params.get('period', 'month')

        # Determinar fecha de inicio según el período
        today = timezone.now().date()
        if period == 'week':
            start_date = today - timedelta(days=today.weekday())
        elif period == 'month':
            start_date = today.replace(day=1)
        elif period == 'year':
            start_date = today.replace(month=1, day=1)
        else:  # 'all' o cualquier otro valor
            start_date = None

        # Filtrar ingresos por período
        queryset = Income.objects.filter(user=user)
        if start_date:
            queryset = queryset.filter(received_at__gte=start_date)

        # Calcular totales por categoría
        summary = queryset.values('category__name', 'currency').annotate(
            total=Sum('amount')
        ).order_by('-total')

        # Calcular total general
        total = queryset.aggregate(Sum('amount'))['amount__sum'] or 0

        return Response({
            'period': period,
            'start_date': start_date,
            'end_date': today,
            'summary': summary,
            'total': total
        })
