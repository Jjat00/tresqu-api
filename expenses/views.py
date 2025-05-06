# expenses/views.py
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
import numpy as np
from openai import OpenAI
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta, datetime
import logging
import calendar

from .models import Expense
from .serializers import ExpenseSerializer

# Configurar logger
logger = logging.getLogger(__name__)


class ExpenseViewSet(viewsets.ModelViewSet):
    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer

    def get_queryset(self):
        """Filtrar gastos por el usuario autenticado"""
        # En caso de que el usuario no esté autenticado, devolver un queryset vacío
        if not self.request.user or not self.request.user.is_authenticated:
            logger.warning(
                "Usuario no autenticado intentando acceder a ExpenseViewSet")
            return Expense.objects.none()

        logger.info(f"Usuario autenticado: {self.request.user.id}")
        return Expense.objects.filter(user=self.request.user)

    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """
        Obtiene el total de gastos agrupados por categoría
        GET /api/expenses/by_category/
        """
        # Registrar información básica para depuración
        logger.info(
            f"Endpoint /by_category/ accedido por usuario: {request.user}")

        queryset = self.get_queryset()

        # Si no hay datos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(f"No hay gastos para el usuario {request.user}")
            return Response({
                'categories': [],
                'totals': []
            })

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

    @action(detail=False, methods=['get'])
    def weekly_by_category(self, request):
        """
        Obtiene los gastos semanales agrupados por categoría para un mes específico
        GET /api/expenses/weekly_by_category/?month=5&year=2023
        """
        # Obtener mes y año de los parámetros de consulta (por defecto, mes y año actual)
        today = timezone.now().date()
        month = int(request.query_params.get('month', today.month))
        year = int(request.query_params.get('year', today.year))

        # Validar el mes (1-12)
        if month < 1 or month > 12:
            return Response(
                {"error": "El mes debe estar entre 1 y 12"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Calcular primer y último día del mes
        first_day = datetime(year, month, 1).date()
        last_day = datetime(
            year, month, calendar.monthrange(year, month)[1]).date()

        # Filtrar gastos del mes especificado para el usuario actual
        queryset = self.get_queryset().filter(
            timestamp__date__gte=first_day,
            timestamp__date__lte=last_day
        )

        # Si no hay datos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay gastos para el usuario {request.user} en {month}/{year}")
            return Response({
                'weeks': [],
                'categories': [],
                'data': []
            })

        # Obtener todas las categorías únicas
        categories = list(set(expense.category.name if expense.category else 'Otros'
                          for expense in queryset))
        if '' in categories:
            categories.remove('')
            if 'Otros' not in categories:
                categories.append('Otros')

        # Inicializar estructura de datos para las semanas
        weeks_data = {}

        # Para cada gasto, asignarlo a la semana correspondiente
        for expense in queryset:
            date = expense.timestamp.date()
            # Determinar el lunes de la semana (fecha de inicio de la semana)
            week_start = date - timedelta(days=date.weekday())
            week_key = week_start.strftime("%d %b")

            # Inicializar la semana si no existe
            if week_key not in weeks_data:
                weeks_data[week_key] = {cat: 0 for cat in categories}

            # Sumar el gasto a la categoría correspondiente
            category = expense.category.name if expense.category else 'Otros'
            if category == '':
                category = 'Otros'

            weeks_data[week_key][category] += expense.amount

        # Ordenar las semanas cronológicamente
        sorted_weeks = sorted(weeks_data.keys(),
                              key=lambda x: datetime.strptime(f"{x} {year}", "%d %b %Y"))

        # Preparar los datos para el frontend
        result = {
            'weeks': [f"Lun {week}" for week in sorted_weeks],
            'categories': categories,
            'data': []
        }

        # Para cada categoría, recopilar sus datos por semana
        for category in categories:
            category_data = []
            for week in sorted_weeks:
                category_data.append(
                    round(float(weeks_data[week].get(category, 0)), 2))

            result['data'].append({
                'name': category,
                'data': category_data
            })

        return Response(result)
