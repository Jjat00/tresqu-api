from django.shortcuts import render
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum
from datetime import datetime, timedelta
from django.utils import timezone
import logging

from .models import Income, IncomeCategory
from .serializers import IncomeSerializer, IncomeCategorySerializer

# Configurar logger
logger = logging.getLogger(__name__)


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

    @action(detail=False, methods=['get'])
    def donut_chart_data(self, request):
        """
        Obtiene ingresos filtrados por categoría y rango de fecha, con formato para gráfica de dona.

        GET /api/income/donut_chart_data/

        Parámetros:
        - category_id: ID de la categoría (opcional, si no se proporciona, se incluyen todas las categorías)
        - date_filter: Filtro de fecha (opcional, valores: 'all', 'today', 'yesterday', 'current_month', 'previous_month', 
                     'current_week', 'previous_week', 'current_year', 'previous_year', 'custom')
        - start_date: Fecha de inicio para filtro personalizado (formato: YYYY-MM-DD)
        - end_date: Fecha de fin para filtro personalizado (formato: YYYY-MM-DD)
        - limit: Número máximo de categorías a mostrar (opcional, por defecto muestra todas)
        """
        logger.info(
            f"Endpoint /donut_chart_data/ accedido por usuario: {request.user}")

        # Obtener queryset base (filtrado por usuario)
        queryset = self.get_queryset()

        # Filtrar por categoría si se proporciona
        category_id = request.query_params.get('category_id')
        if category_id:
            try:
                category_id = int(category_id)
                queryset = queryset.filter(category_id=category_id)
                logger.info(f"Filtrando por categoría: {category_id}")
            except ValueError:
                return Response(
                    {"error": "ID de categoría inválido"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Aplicar filtro de fecha
        date_filter = request.query_params.get('date_filter', 'all')
        today = timezone.now().date()

        if date_filter == 'today':
            # Hoy
            start_date = today
            end_date = today

        elif date_filter == 'yesterday':
            # Ayer
            start_date = today - timedelta(days=1)
            end_date = start_date

        elif date_filter == 'current_month':
            # Mes actual
            start_date = today.replace(day=1)
            next_month = today.month + 1 if today.month < 12 else 1
            next_month_year = today.year if today.month < 12 else today.year + 1
            end_date = datetime(next_month_year, next_month,
                                1).date() - timedelta(days=1)

        elif date_filter == 'previous_month':
            # Mes anterior
            first_day_current = today.replace(day=1)
            last_day_previous = first_day_current - timedelta(days=1)
            start_date = last_day_previous.replace(day=1)
            end_date = last_day_previous

        elif date_filter == 'current_week':
            # Semana actual (lunes a domingo)
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)

        elif date_filter == 'previous_week':
            # Semana anterior (lunes a domingo)
            start_date = today - timedelta(days=today.weekday() + 7)
            end_date = start_date + timedelta(days=6)

        elif date_filter == 'current_year':
            # Año actual
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)

        elif date_filter == 'previous_year':
            # Año anterior
            start_date = datetime(today.year - 1, 1, 1).date()
            end_date = datetime(today.year - 1, 12, 31).date()

        elif date_filter == 'custom':
            # Filtro personalizado
            try:
                start_date = datetime.strptime(
                    request.query_params.get('start_date'),
                    '%Y-%m-%d'
                ).date()
                end_date = datetime.strptime(
                    request.query_params.get('end_date'),
                    '%Y-%m-%d'
                ).date()

                if start_date > end_date:
                    return Response(
                        {"error": "La fecha de inicio debe ser anterior a la fecha de fin"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                return Response(
                    {"error": "Formato de fecha inválido. Use YYYY-MM-DD"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # 'all' o cualquier otro valor: sin filtro de fecha
            start_date = None
            end_date = None

        # Aplicar filtro de fecha al queryset
        if start_date and end_date:
            logger.info(
                f"Filtrando por rango de fecha: {start_date} a {end_date}")
            queryset = queryset.filter(
                timestamp__date__gte=start_date,
                timestamp__date__lte=end_date
            )

        # Si no hay ingresos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay ingresos para el usuario {request.user} con los filtros aplicados")
            return Response({
                'labels': [],
                'datasets': [{
                    'data': [],
                    'backgroundColor': [],
                    'hoverBackgroundColor': []
                }],
                'filter_summary': "Sin datos",
                'total_amount': 0
            })

        # Agrupar por categoría y calcular totales
        by_category = queryset.values('category__name').annotate(
            total=Sum('amount')
        ).order_by('-total')

        # Limitar el número de categorías si se solicita
        limit = request.query_params.get('limit')
        if limit:
            try:
                limit = int(limit)
                by_category = by_category[:limit]
            except ValueError:
                pass  # Ignorar si no es un entero válido

        # Transformar a formato para gráficos de dona (compatible con Chart.js)
        labels = []
        data = []
        backgroundColor = []
        hoverBackgroundColor = []

        for item in by_category:
            category_name = item['category__name'] or 'Sin categoría'
            category = IncomeCategory.objects.filter(
                name=category_name).first()
            color = category.color if category and hasattr(
                category, 'color') else '#4CAF50'  # Verde por defecto para ingresos

            labels.append(category_name)
            data.append(float(item['total']))
            backgroundColor.append(color)
            hoverBackgroundColor.append(color)

        # Obtener lista simplificada de ingresos para detalles
        incomes = self.get_serializer(
            # Mostrar solo los 10 más recientes
            queryset.order_by('-timestamp')[:10],
            many=True
        ).data

        # Resumen del filtro aplicado
        filter_summary = "Todos los ingresos"
        if date_filter != 'all' and start_date and end_date:
            filter_summary = f"Ingresos del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"

        # Formato adaptado para gráfica de dona con Chart.js
        response_data = {
            'labels': labels,
            'datasets': [{
                'data': data,
                'backgroundColor': backgroundColor,
                'hoverBackgroundColor': hoverBackgroundColor
            }],
            'filter_summary': filter_summary,
            'total_amount': sum(data),
            'recent_incomes': incomes  # Incluir algunos ingresos recientes para detalles
        }

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def bar_chart_data(self, request):
        """
        Obtiene datos para una gráfica de barras de ingresos por categoría, con las mismas
        opciones de filtrado que donut_chart_data.

        GET /api/income/bar_chart_data/

        Parámetros:
        - category_id: ID de la categoría (opcional, si no se proporciona, se incluyen todas las categorías)
        - date_filter: Filtro de fecha (opcional, valores: 'all', 'today', 'yesterday', 'current_month', 'previous_month', 
                     'current_week', 'previous_week', 'current_year', 'previous_year', 'custom')
        - start_date: Fecha de inicio para filtro personalizado (formato: YYYY-MM-DD)
        - end_date: Fecha de fin para filtro personalizado (formato: YYYY-MM-DD)
        - limit: Número máximo de categorías a mostrar (opcional, por defecto muestra todas)
        """
        logger.info(
            f"Endpoint /bar_chart_data/ accedido por usuario: {request.user}")

        # Obtener queryset base (filtrado por usuario)
        queryset = self.get_queryset()

        # Filtrar por categoría si se proporciona
        category_id = request.query_params.get('category_id')
        if category_id:
            try:
                category_id = int(category_id)
                queryset = queryset.filter(category_id=category_id)
                logger.info(f"Filtrando por categoría: {category_id}")
            except ValueError:
                return Response(
                    {"error": "ID de categoría inválido"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Aplicar filtro de fecha
        date_filter = request.query_params.get('date_filter', 'all')
        today = timezone.now().date()

        if date_filter == 'today':
            # Hoy
            start_date = today
            end_date = today

        elif date_filter == 'yesterday':
            # Ayer
            start_date = today - timedelta(days=1)
            end_date = start_date

        elif date_filter == 'current_month':
            # Mes actual
            start_date = today.replace(day=1)
            next_month = today.month + 1 if today.month < 12 else 1
            next_month_year = today.year if today.month < 12 else today.year + 1
            end_date = datetime(next_month_year, next_month,
                                1).date() - timedelta(days=1)

        elif date_filter == 'previous_month':
            # Mes anterior
            first_day_current = today.replace(day=1)
            last_day_previous = first_day_current - timedelta(days=1)
            start_date = last_day_previous.replace(day=1)
            end_date = last_day_previous

        elif date_filter == 'current_week':
            # Semana actual (lunes a domingo)
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)

        elif date_filter == 'previous_week':
            # Semana anterior (lunes a domingo)
            start_date = today - timedelta(days=today.weekday() + 7)
            end_date = start_date + timedelta(days=6)

        elif date_filter == 'current_year':
            # Año actual
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)

        elif date_filter == 'previous_year':
            # Año anterior
            start_date = datetime(today.year - 1, 1, 1).date()
            end_date = datetime(today.year - 1, 12, 31).date()

        elif date_filter == 'custom':
            # Filtro personalizado
            try:
                start_date = datetime.strptime(
                    request.query_params.get('start_date'),
                    '%Y-%m-%d'
                ).date()
                end_date = datetime.strptime(
                    request.query_params.get('end_date'),
                    '%Y-%m-%d'
                ).date()

                if start_date > end_date:
                    return Response(
                        {"error": "La fecha de inicio debe ser anterior a la fecha de fin"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                return Response(
                    {"error": "Formato de fecha inválido. Use YYYY-MM-DD"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # 'all' o cualquier otro valor: sin filtro de fecha
            start_date = None
            end_date = None

        # Aplicar filtro de fecha al queryset
        if start_date and end_date:
            logger.info(
                f"Filtrando por rango de fecha: {start_date} a {end_date}")
            queryset = queryset.filter(
                timestamp__date__gte=start_date,
                timestamp__date__lte=end_date
            )

        # Si no hay ingresos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay ingresos para el usuario {request.user} con los filtros aplicados")
            return Response({
                'labels': [],
                'datasets': []
            })

        # Agrupar por categoría y calcular totales
        by_category = queryset.values('category__name').annotate(
            total=Sum('amount')
        ).order_by('-total')

        # Limitar el número de categorías si se solicita
        limit = request.query_params.get('limit')
        if limit:
            try:
                limit = int(limit)
                by_category = by_category[:limit]
            except ValueError:
                pass  # Ignorar si no es un entero válido

        # Transformar a formato para gráficas de barras (compatible con Chart.js)
        labels = []
        data = []
        colors = []

        for item in by_category:
            category_name = item['category__name'] or 'Sin categoría'
            category = IncomeCategory.objects.filter(
                name=category_name).first()
            color = category.color if category and hasattr(
                category, 'color') else '#4CAF50'  # Verde por defecto para ingresos

            labels.append(category_name)
            data.append(float(item['total']))
            colors.append(color)

        # Resumen del filtro aplicado
        filter_summary = "Todos los ingresos"
        if date_filter != 'all' and start_date and end_date:
            filter_summary = f"Ingresos del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"

        # Formato listo para usar en Chart.js
        response_data = {
            'labels': labels,
            'datasets': [{
                'label': 'Ingresos por categoría',
                'data': data,
                'backgroundColor': colors,
                'borderColor': colors,
                'borderWidth': 1
            }],
            'filter_summary': filter_summary,
            'total_amount': sum(data),
            'recent_incomes': self.get_serializer(
                # Mostrar solo los 10 más recientes
                queryset.order_by('-timestamp')[:10],
                many=True
            ).data
        }

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def line_chart_data(self, request):
        """
        Obtiene datos para una gráfica de línea que muestra el total de ingresos a lo largo del tiempo.

        GET /api/income/line_chart_data/

        Parámetros:
        - category_id: ID de la categoría (opcional, si no se proporciona, se incluyen todas las categorías)
        - date_filter: Filtro de fecha (opcional, valores: 'all', 'today', 'yesterday', 'current_month', 'previous_month', 
                     'current_week', 'previous_week', 'current_year', 'previous_year', 'custom')
        - start_date: Fecha de inicio para filtro personalizado (formato: YYYY-MM-DD)
        - end_date: Fecha de fin para filtro personalizado (formato: YYYY-MM-DD)
        - group_by: Agrupación temporal (opcional, valores: 'day', 'week', 'month', por defecto varía según el filtro)
        """
        logger.info(
            f"Endpoint /line_chart_data/ accedido por usuario: {request.user}")

        # Obtener queryset base (filtrado por usuario)
        queryset = self.get_queryset()

        # Filtrar por categoría si se proporciona
        category_id = request.query_params.get('category_id')
        if category_id:
            try:
                category_id = int(category_id)
                queryset = queryset.filter(category_id=category_id)
                logger.info(f"Filtrando por categoría: {category_id}")
            except ValueError:
                return Response(
                    {"error": "ID de categoría inválido"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Aplicar filtro de fecha
        date_filter = request.query_params.get('date_filter', 'all')
        today = timezone.now().date()

        if date_filter == 'today':
            # Hoy
            start_date = today
            end_date = today

        elif date_filter == 'yesterday':
            # Ayer
            start_date = today - timedelta(days=1)
            end_date = start_date

        elif date_filter == 'current_month':
            # Mes actual
            start_date = today.replace(day=1)
            next_month = today.month + 1 if today.month < 12 else 1
            next_month_year = today.year if today.month < 12 else today.year + 1
            end_date = datetime(next_month_year, next_month,
                                1).date() - timedelta(days=1)

        elif date_filter == 'previous_month':
            # Mes anterior
            first_day_current = today.replace(day=1)
            last_day_previous = first_day_current - timedelta(days=1)
            start_date = last_day_previous.replace(day=1)
            end_date = last_day_previous

        elif date_filter == 'current_week':
            # Semana actual (lunes a domingo)
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)

        elif date_filter == 'previous_week':
            # Semana anterior (lunes a domingo)
            start_date = today - timedelta(days=today.weekday() + 7)
            end_date = start_date + timedelta(days=6)

        elif date_filter == 'current_year':
            # Año actual
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)

        elif date_filter == 'previous_year':
            # Año anterior
            start_date = datetime(today.year - 1, 1, 1).date()
            end_date = datetime(today.year - 1, 12, 31).date()

        elif date_filter == 'custom':
            # Filtro personalizado
            try:
                start_date = datetime.strptime(
                    request.query_params.get('start_date'),
                    '%Y-%m-%d'
                ).date()
                end_date = datetime.strptime(
                    request.query_params.get('end_date'),
                    '%Y-%m-%d'
                ).date()

                if start_date > end_date:
                    return Response(
                        {"error": "La fecha de inicio debe ser anterior a la fecha de fin"},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                return Response(
                    {"error": "Formato de fecha inválido. Use YYYY-MM-DD"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # 'all' o cualquier otro valor: usar último año
            end_date = today
            start_date = end_date - timedelta(days=365)

        # Aplicar filtro de fecha al queryset
        queryset = queryset.filter(
            timestamp__date__gte=start_date,
            timestamp__date__lte=end_date
        )

        # Si no hay ingresos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay ingresos para el usuario {request.user} con los filtros aplicados")
            return Response({
                'labels': [],
                'datasets': [{
                    'label': 'Total de ingresos',
                    'data': [],
                    'backgroundColor': 'rgba(76, 175, 80, 0.2)',
                    'borderColor': 'rgba(76, 175, 80, 1)',
                    'borderWidth': 2,
                    'fill': False,
                    'tension': 0.1
                }]
            })

        # Determinar agrupación temporal
        group_by = request.query_params.get('group_by')

        # Ajustar automáticamente la agrupación según el filtro de fecha si no se especifica
        if not group_by:
            if date_filter in ['today', 'yesterday']:
                group_by = 'hour'
            elif date_filter in ['current_week', 'previous_week']:
                group_by = 'day'
            elif date_filter in ['current_month', 'previous_month']:
                group_by = 'day'
            elif (end_date - start_date).days <= 60:
                group_by = 'day'
            elif (end_date - start_date).days <= 365:
                group_by = 'week'
            else:
                group_by = 'month'

        # Función para formatear la fecha según la agrupación
        def format_date_label(date, group):
            if group == 'hour':
                return date.strftime('%H:%M')
            elif group == 'day':
                return date.strftime('%d/%m')
            elif group == 'week':
                # Lunes de la semana
                week_start = date - timedelta(days=date.weekday())
                return f"{week_start.strftime('%d/%m')}"
            else:  # 'month' (por defecto)
                return date.strftime('%b %Y')

        # Función para obtener la clave de agrupación temporal
        def get_time_key(date_time, group):
            date = date_time.date()
            if group == 'hour':
                # Redondear a la hora
                return datetime(date.year, date.month, date.day, date_time.hour, 0, 0)
            elif group == 'day':
                return date
            elif group == 'week':
                # Lunes de la semana
                week_start = date - timedelta(days=date.weekday())
                return week_start
            else:  # 'month' (por defecto)
                return date.replace(day=1)

        # Preparar datos para la gráfica
        time_labels = []
        totals_data = []

        # Generar períodos de tiempo para el rango de fechas y diccionario para acumular ingresos
        time_totals = {}

        if group_by == 'hour' and (end_date - start_date).days <= 1:
            # Horas para hoy o ayer (solo si es un día)
            start_datetime = datetime.combine(start_date, datetime.min.time())
            end_datetime = datetime.combine(end_date, datetime.max.time())
            current = start_datetime

            while current <= end_datetime:
                label = format_date_label(current, group_by)
                time_labels.append(label)
                time_totals[label] = 0
                current += timedelta(hours=1)

        elif group_by == 'day':
            current = start_date
            while current <= end_date:
                label = format_date_label(current, group_by)
                time_labels.append(label)
                time_totals[label] = 0
                current += timedelta(days=1)

        elif group_by == 'week':
            # Ajustar a lunes
            current = start_date - timedelta(days=start_date.weekday())
            while current <= end_date:
                label = format_date_label(current, group_by)
                time_labels.append(label)
                time_totals[label] = 0
                current += timedelta(days=7)

        else:  # 'month' (por defecto)
            current = start_date.replace(day=1)
            while current <= end_date:
                label = format_date_label(current, group_by)
                time_labels.append(label)
                time_totals[label] = 0

                # Avanzar al siguiente mes
                if current.month == 12:
                    current = datetime(current.year + 1, 1, 1).date()
                else:
                    current = datetime(
                        current.year, current.month + 1, 1).date()

        # Agrupar y sumar ingresos por período de tiempo
        for income in queryset:
            time_key = format_date_label(get_time_key(
                income.timestamp, group_by), group_by)
            if time_key in time_totals:
                time_totals[time_key] += float(income.amount)

        # Convertir el diccionario de totales a lista manteniendo el orden de las etiquetas
        for label in time_labels:
            totals_data.append(round(time_totals[label], 2))

        # Resumen del filtro aplicado
        filter_summary = "Ingresos del año"
        if date_filter != 'all' and start_date and end_date:
            filter_summary = f"Ingresos del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"

        # Formato final para Chart.js (una sola línea con los totales)
        response_data = {
            'labels': time_labels,
            'datasets': [{
                'label': 'Total de ingresos',
                'data': totals_data,
                'backgroundColor': 'rgba(76, 175, 80, 0.2)',
                'borderColor': 'rgba(76, 175, 80, 1)',
                'borderWidth': 2,
                'fill': True,
                'tension': 0.1
            }],
            'filter_summary': filter_summary,
            'total_amount': round(sum(totals_data), 2)
        }

        return Response(response_data)
