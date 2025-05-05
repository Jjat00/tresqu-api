# expenses/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
import numpy as np
from openai import OpenAI
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta
import datetime

from .models import Expense
from .serializers import ExpenseSerializer


class ExpenseViewSet(viewsets.ModelViewSet):
    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer

    def get_queryset(self):
        """Filtrar gastos por el usuario autenticado"""
        return Expense.objects.filter(user=self.request.user)

    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """
        Obtiene el total de gastos agrupados por categoría
        GET /api/expenses/by_category/
        """
        queryset = self.get_queryset()
        result = queryset.values('category__name').annotate(
            total=Sum('amount')
        ).order_by('-total')

        # Transformar a formato esperado por el frontend
        categories = []
        totals = []
        for item in result:
            category_name = item['category__name'] or 'Otros'
            categories.append(category_name)
            totals.append(float(item['total']))

        return Response({
            'categories': categories,
            'totals': totals
        })

    @action(detail=False, methods=['get'])
    def by_week(self, request):
        """
        Obtiene gastos semanales por categoría
        GET /api/expenses/by_week/?months=1
        """
        months = int(request.query_params.get('months', 1))

        # Calcular fecha de inicio (hace X meses)
        start_date = timezone.now().date().replace(day=1)
        if months > 0:
            for _ in range(months - 1):
                # Retroceder al primer día del mes anterior
                start_date = (start_date - timedelta(days=1)).replace(day=1)

        queryset = self.get_queryset().filter(timestamp__gte=start_date)

        # Agrupar por semana y categoría
        weekly_data = {}
        categories = set()

        for expense in queryset:
            # Obtener el inicio de la semana (lunes)
            expense_date = expense.timestamp.date()
            week_start = expense_date - timedelta(days=expense_date.weekday())
            week_key = week_start.strftime('%d %b')

            category = expense.category.name if expense.category else expense.category_str or 'Otros'
            categories.add(category)

            if week_key not in weekly_data:
                weekly_data[week_key] = {}

            if category not in weekly_data[week_key]:
                weekly_data[week_key][category] = 0

            weekly_data[week_key][category] += float(expense.amount)

        # Ordenar por semana
        weeks = sorted(weekly_data.keys(),
                       key=lambda x: datetime.datetime.strptime(x, '%d %b'))
        categories = sorted(list(categories))

        result = {
            'weeks': weeks,
            'categories': categories,
            'data': {}
        }

        for category in categories:
            result['data'][category] = []
            for week in weeks:
                result['data'][category].append(
                    weekly_data[week].get(category, 0)
                )

        return Response(result)

    @action(detail=False, methods=['get'])
    def recent(self, request):
        """
        Obtiene los gastos más recientes
        GET /api/expenses/recent/?limit=10
        """
        limit = int(request.query_params.get('limit', 10))
        queryset = self.get_queryset().order_by('-timestamp')[:limit]
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """
        Obtiene un resumen completo para el dashboard
        GET /api/expenses/summary/?months=1
        """
        months = int(request.query_params.get('months', 1))

        # Calcular fecha de inicio (hace X meses)
        start_date = timezone.now().date().replace(day=1)
        if months > 0:
            for _ in range(months - 1):
                # Retroceder al primer día del mes anterior
                start_date = (start_date - timedelta(days=1)).replace(day=1)

        queryset = self.get_queryset().filter(timestamp__gte=start_date)

        # Gastos por categoría
        by_category = queryset.values('category__name').annotate(
            total=Sum('amount')
        ).order_by('-total')

        # Transformar datos de categorías para el gráfico de pastel
        categories_data = {}
        for item in by_category:
            category_name = item['category__name'] or 'Otros'
            categories_data[category_name] = float(item['total'])

        # Total de gastos
        total_expenses = queryset.aggregate(total=Sum('amount'))['total'] or 0

        # Gastos recientes
        recent_expenses = self.get_serializer(
            queryset.order_by('-timestamp')[:10],
            many=True
        ).data

        return Response({
            'by_category': categories_data,
            'total': float(total_expenses),
            'recent_expenses': recent_expenses,
        })

    @action(detail=False, methods=['post'])
    def find_similar(self, request):
        """
        Busca gastos similares basados en una descripción de texto
        POST /api/expenses/find_similar/
        {
            "description": "Compra de café en Starbucks"
        }
        """
        description = request.data.get('description')
        if not description:
            return Response(
                {"error": "Se requiere una descripción"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generar embedding para la descripción
        embedding = self._generate_embedding(description)
        if not embedding:
            return Response(
                {"error": "No se pudo generar el embedding"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Buscar gastos similares
        user = request.user
        similar_expenses = Expense.find_similar(
            user=user,
            embedding=embedding,
            limit=5
        )

        # Serializar y devolver resultados
        serializer = self.get_serializer(similar_expenses, many=True)
        return Response(serializer.data)

    def _generate_embedding(self, text):
        """
        Genera un embedding para el texto dado usando la API de OpenAI
        """
        try:
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = client.embeddings.create(
                input=text,
                model="text-embedding-3-small"
            )
            embedding = response.data[0].embedding
            return np.array(embedding)
        except Exception as e:
            print(f"Error al generar embedding: {e}")
            return None
