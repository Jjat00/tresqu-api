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
from django.db.models import Q
import pytz

from .models import Expense, Category
from .serializers import ExpenseSerializer

# Configurar logger
logger = logging.getLogger(__name__)

# Zona horaria predeterminada (UTC-5)
DEFAULT_TIMEZONE = pytz.timezone('America/Bogota')  # Equivalente a UTC-5


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

    def _get_user_timezone(self, request):
        """Obtiene la zona horaria del usuario desde los parámetros o usa el predeterminado"""
        # El parámetro debe ser un string de zona horaria válido o un offset en horas
        tz_param = request.query_params.get('timezone')
        if tz_param:
            try:
                # Intenta interpretar como nombre de zona horaria (ej. 'America/Bogota')
                user_timezone = pytz.timezone(tz_param)
            except pytz.exceptions.UnknownTimeZoneError:
                try:
                    # Intenta interpretar como offset numérico (ej. '-5')
                    offset_hours = int(tz_param)
                    offset = timedelta(hours=offset_hours)
                    # Crear zona horaria con el offset
                    user_timezone = pytz.FixedOffset(
                        offset.total_seconds() // 60)
                except ValueError:
                    # Si hay error, usar predeterminado
                    user_timezone = DEFAULT_TIMEZONE
        else:
            # Si no se proporciona, usar predeterminado (UTC-5)
            user_timezone = DEFAULT_TIMEZONE

        return user_timezone

    def _get_local_datetime(self, request):
        """Obtiene la fecha y hora actual en la zona horaria del usuario"""
        user_timezone = self._get_user_timezone(request)
        # Obtener datetime actual en UTC
        utc_now = timezone.now()
        # Convertir a la zona horaria del usuario
        local_now = utc_now.astimezone(user_timezone)
        return local_now

    def _convert_to_utc(self, local_date, is_end_date=False, user_timezone=None):
        """
        Convierte una fecha local a UTC para consultas en la base de datos

        Args:
            local_date: Fecha en la zona horaria local (solo fecha, sin hora)
            is_end_date: Si es True, se establece la hora al final del día (23:59:59)
            user_timezone: La zona horaria del usuario, si es None se usa el predeterminado

        Returns:
            Fecha y hora en UTC para filtrar correctamente en la base de datos
        """
        if user_timezone is None:
            user_timezone = DEFAULT_TIMEZONE

        # Establecer hora según si es inicio o fin del día
        if is_end_date:
            # Fin del día (23:59:59)
            local_datetime = datetime.combine(local_date, datetime.max.time())
        else:
            # Inicio del día (00:00:00)
            local_datetime = datetime.combine(local_date, datetime.min.time())

        # Asignar zona horaria
        local_datetime = user_timezone.localize(local_datetime)

        # Convertir a UTC
        utc_datetime = local_datetime.astimezone(pytz.UTC)

        return utc_datetime

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
            return Response([])

        # Obtener todas las categorías disponibles para el usuario
        all_categories = list(set(expense.category.name if expense.category else 'Otros'
                                  for expense in queryset))
        if '' in all_categories:
            all_categories.remove('')
            if 'Otros' not in all_categories:
                all_categories.append('Otros')

        # Identificar todas las semanas del mes
        current_date = first_day
        all_weeks = []

        while current_date <= last_day:
            # Encontrar el lunes de la semana actual
            week_start = current_date - timedelta(days=current_date.weekday())
            week_key = week_start.strftime("%d %b")

            if week_key not in all_weeks:
                all_weeks.append(week_key)

            # Avanzar al siguiente día
            current_date += timedelta(days=1)

        # Ordenar las semanas cronológicamente
        all_weeks = sorted(all_weeks,
                           key=lambda x: datetime.strptime(f"{x} {year}", "%d %b %Y"))

        # Inicializar datos de gastos por semana y categoría
        weekly_data = []

        # Preparar estructura para cada semana
        for week in all_weeks:
            week_data = {
                'week': f"Lun {week}",
                'totals': {cat: 0 for cat in all_categories}
            }
            weekly_data.append(week_data)

        # Distribuir gastos en las semanas correspondientes
        for expense in queryset:
            date = expense.timestamp.date()
            # Determinar el lunes de la semana
            week_start = date - timedelta(days=date.weekday())
            week_key = week_start.strftime("%d %b")

            # Encontrar el índice de la semana en weekly_data
            week_index = all_weeks.index(week_key)

            # Categoría del gasto
            category = expense.category.name if expense.category and expense.category.name else 'Otros'
            if category == '':
                category = 'Otros'

            # Sumar el gasto a la categoría correspondiente
            weekly_data[week_index]['totals'][category] += expense.amount

        # Redondear valores numéricos
        for week_data in weekly_data:
            for category in week_data['totals']:
                week_data['totals'][category] = round(
                    float(week_data['totals'][category]), 2)

        return Response(weekly_data)

    @action(detail=False, methods=['get'])
    def donut_chart_data(self, request):
        """
        Obtiene gastos filtrados por categoría y rango de fecha, con formato para gráfica de dona.

        GET /api/expenses/donut_chart_data/

        Parámetros:
        - category_id: ID de la categoría (opcional, si no se proporciona, se incluyen todas las categorías)
        - date_filter: Filtro de fecha (opcional, valores: 'all', 'today', 'yesterday', 'current_month', 'previous_month', 
                     'current_week', 'previous_week', 'current_year', 'previous_year', 'custom')
        - start_date: Fecha de inicio para filtro personalizado (formato: YYYY-MM-DD)
        - end_date: Fecha de fin para filtro personalizado (formato: YYYY-MM-DD)
        - limit: Número máximo de categorías a mostrar (opcional, por defecto muestra todas)
        - timezone: Zona horaria del usuario (opcional, por defecto 'America/Bogota' o UTC-5)
        """
        logger.info(
            f"Endpoint /donut_chart_data/ accedido por usuario: {request.user}")

        # Obtener queryset base (filtrado por usuario)
        queryset = self.get_queryset()

        # Obtener zona horaria del usuario
        user_timezone = self._get_user_timezone(request)
        # Obtener fecha/hora actual en la zona horaria del usuario
        local_now = self._get_local_datetime(request)
        today = local_now.date()

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

        if date_filter == 'today':
            # Hoy (en la zona horaria del usuario)
            start_date = today
            end_date = today
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'yesterday':
            # Ayer (en la zona horaria del usuario)
            start_date = today - timedelta(days=1)
            end_date = start_date
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'current_month':
            # Mes actual (en la zona horaria del usuario)
            start_date = today.replace(day=1)
            if today.month == 12:
                next_month = 1
                next_month_year = today.year + 1
            else:
                next_month = today.month + 1
                next_month_year = today.year

            end_date = datetime(next_month_year, next_month,
                                1).date() - timedelta(days=1)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'previous_month':
            # Mes anterior (en la zona horaria del usuario)
            first_day_current = today.replace(day=1)
            last_day_previous = first_day_current - timedelta(days=1)
            start_date = last_day_previous.replace(day=1)
            end_date = last_day_previous

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'current_week':
            # Semana actual (lunes a domingo en la zona horaria del usuario)
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'previous_week':
            # Semana anterior (en la zona horaria del usuario)
            start_date = today - timedelta(days=today.weekday() + 7)
            end_date = start_date + timedelta(days=6)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'current_year':
            # Año actual (en la zona horaria del usuario)
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'previous_year':
            # Año anterior (en la zona horaria del usuario)
            start_date = datetime(today.year - 1, 1, 1).date()
            end_date = datetime(today.year - 1, 12, 31).date()

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'custom':
            # Filtro personalizado (considerando zona horaria del usuario)
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

                # Convertir a UTC para filtrar correctamente
                start_datetime = self._convert_to_utc(
                    start_date, False, user_timezone)
                end_datetime = self._convert_to_utc(
                    end_date, True, user_timezone)

                queryset = queryset.filter(
                    timestamp__gte=start_datetime,
                    timestamp__lte=end_datetime
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

        # Si no hay gastos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay gastos para el usuario {request.user} con los filtros aplicados")
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
            category_name = item['category__name'] or 'Otros'
            category = Category.objects.filter(name=category_name).first()
            color = category.color if category else '#CCCCCC'

            labels.append(category_name)
            data.append(float(item['total']))
            backgroundColor.append(color)
            hoverBackgroundColor.append(color)

        # Obtener lista simplificada de gastos para detalles
        expenses = self.get_serializer(
            # Mostrar solo los 10 más recientes
            queryset.order_by('-timestamp')[:10],
            many=True
        ).data

        # Resumen del filtro aplicado
        filter_summary = "Todos los gastos"
        if date_filter != 'all' and start_date and end_date:
            filter_summary = f"Gastos del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"

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
            'recent_expenses': expenses  # Incluir algunos gastos recientes para detalles
        }

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def bar_chart_data(self, request):
        """
        Obtiene datos para una gráfica de barras de gastos por categoría, con las mismas
        opciones de filtrado que donut_chart_data.

        GET /api/expenses/bar_chart_data/

        Parámetros:
        - category_id: ID de la categoría (opcional, si no se proporciona, se incluyen todas las categorías)
        - date_filter: Filtro de fecha (opcional, valores: 'all', 'today', 'yesterday', 'current_month', 'previous_month', 
                     'current_week', 'previous_week', 'current_year', 'previous_year', 'custom')
        - start_date: Fecha de inicio para filtro personalizado (formato: YYYY-MM-DD)
        - end_date: Fecha de fin para filtro personalizado (formato: YYYY-MM-DD)
        - limit: Número máximo de categorías a mostrar (opcional, por defecto muestra todas)
        - timezone: Zona horaria del usuario (opcional, por defecto 'America/Bogota' o UTC-5)
        """
        logger.info(
            f"Endpoint /bar_chart_data/ accedido por usuario: {request.user}")

        # Obtener queryset base (filtrado por usuario)
        queryset = self.get_queryset()

        # Obtener zona horaria del usuario
        user_timezone = self._get_user_timezone(request)
        # Obtener fecha/hora actual en la zona horaria del usuario
        local_now = self._get_local_datetime(request)
        today = local_now.date()

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

        if date_filter == 'today':
            # Hoy (en la zona horaria del usuario)
            start_date = today
            end_date = today
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'yesterday':
            # Ayer (en la zona horaria del usuario)
            start_date = today - timedelta(days=1)
            end_date = start_date
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'current_month':
            # Mes actual (en la zona horaria del usuario)
            start_date = today.replace(day=1)
            if today.month == 12:
                next_month = 1
                next_month_year = today.year + 1
            else:
                next_month = today.month + 1
                next_month_year = today.year

            end_date = datetime(next_month_year, next_month,
                                1).date() - timedelta(days=1)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'previous_month':
            # Mes anterior (en la zona horaria del usuario)
            first_day_current = today.replace(day=1)
            last_day_previous = first_day_current - timedelta(days=1)
            start_date = last_day_previous.replace(day=1)
            end_date = last_day_previous

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'current_week':
            # Semana actual (lunes a domingo en la zona horaria del usuario)
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'previous_week':
            # Semana anterior (en la zona horaria del usuario)
            start_date = today - timedelta(days=today.weekday() + 7)
            end_date = start_date + timedelta(days=6)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'current_year':
            # Año actual (en la zona horaria del usuario)
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'previous_year':
            # Año anterior (en la zona horaria del usuario)
            start_date = datetime(today.year - 1, 1, 1).date()
            end_date = datetime(today.year - 1, 12, 31).date()

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

        elif date_filter == 'custom':
            # Filtro personalizado (considerando zona horaria del usuario)
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

                # Convertir a UTC para filtrar correctamente
                start_datetime = self._convert_to_utc(
                    start_date, False, user_timezone)
                end_datetime = self._convert_to_utc(
                    end_date, True, user_timezone)

                queryset = queryset.filter(
                    timestamp__gte=start_datetime,
                    timestamp__lte=end_datetime
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

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        # Si no hay gastos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay gastos para el usuario {request.user} con los filtros aplicados")
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
            category_name = item['category__name'] or 'Otros'
            category = Category.objects.filter(name=category_name).first()
            color = category.color if category else '#CCCCCC'

            labels.append(category_name)
            data.append(float(item['total']))
            colors.append(color)

        # Resumen del filtro aplicado
        filter_summary = "Todos los gastos"
        if date_filter != 'all' and start_date and end_date:
            filter_summary = f"Gastos del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"

        # Formato listo para usar en Chart.js
        response_data = {
            'labels': labels,
            'datasets': [{
                'label': 'Gastos por categoría',
                'data': data,
                'backgroundColor': colors,
                'borderColor': colors,
                'borderWidth': 1
            }],
            'filter_summary': filter_summary,
            'total_amount': sum(data),
            'recent_expenses': self.get_serializer(
                # Mostrar solo los 10 más recientes
                queryset.order_by('-timestamp')[:10],
                many=True
            ).data
        }

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def line_chart_data(self, request):
        """
        Obtiene datos para una gráfica de línea que muestra el total de gastos a lo largo del tiempo.

        GET /api/expenses/line_chart_data/

        Parámetros:
        - category_id: ID de la categoría (opcional, si no se proporciona, se incluyen todas las categorías)
        - date_filter: Filtro de fecha (opcional, valores: 'all', 'today', 'yesterday', 'current_month', 'previous_month', 
                     'current_week', 'previous_week', 'current_year', 'previous_year', 'custom')
        - start_date: Fecha de inicio para filtro personalizado (formato: YYYY-MM-DD)
        - end_date: Fecha de fin para filtro personalizado (formato: YYYY-MM-DD)
        - group_by: Agrupación temporal (opcional, valores: 'day', 'week', 'month', por defecto varía según el filtro)
        - timezone: Zona horaria del usuario (opcional, por defecto 'America/Bogota' o UTC-5)
        """
        logger.info(
            f"Endpoint /line_chart_data/ accedido por usuario: {request.user}")

        # Obtener queryset base (filtrado por usuario)
        queryset = self.get_queryset()

        # Obtener zona horaria del usuario
        user_timezone = self._get_user_timezone(request)
        # Obtener fecha/hora actual en la zona horaria del usuario
        local_now = self._get_local_datetime(request)
        today = local_now.date()

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

        if date_filter == 'today':
            # Hoy (en la zona horaria del usuario)
            start_date = today
            end_date = today
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'yesterday':
            # Ayer (en la zona horaria del usuario)
            start_date = today - timedelta(days=1)
            end_date = start_date
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'current_month':
            # Mes actual (en la zona horaria del usuario)
            start_date = today.replace(day=1)
            if today.month == 12:
                next_month = 1
                next_month_year = today.year + 1
            else:
                next_month = today.month + 1
                next_month_year = today.year

            end_date = datetime(next_month_year, next_month,
                                1).date() - timedelta(days=1)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'previous_month':
            # Mes anterior (en la zona horaria del usuario)
            first_day_current = today.replace(day=1)
            last_day_previous = first_day_current - timedelta(days=1)
            start_date = last_day_previous.replace(day=1)
            end_date = last_day_previous

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'current_week':
            # Semana actual (lunes a domingo en la zona horaria del usuario)
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'previous_week':
            # Semana anterior (en la zona horaria del usuario)
            start_date = today - timedelta(days=today.weekday() + 7)
            end_date = start_date + timedelta(days=6)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'current_year':
            # Año actual (en la zona horaria del usuario)
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'previous_year':
            # Año anterior (en la zona horaria del usuario)
            start_date = datetime(today.year - 1, 1, 1).date()
            end_date = datetime(today.year - 1, 12, 31).date()

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'custom':
            # Filtro personalizado (considerando zona horaria del usuario)
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

                # Convertir a UTC para filtrar correctamente
                start_datetime = self._convert_to_utc(
                    start_date, False, user_timezone)
                end_datetime = self._convert_to_utc(
                    end_date, True, user_timezone)

            except (ValueError, TypeError):
                return Response(
                    {"error": "Formato de fecha inválido. Use YYYY-MM-DD"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # 'all' o cualquier otro valor: usar último año
            end_date = today
            start_date = end_date - timedelta(days=365)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        # Aplicar filtro de fecha al queryset
        queryset = queryset.filter(
            timestamp__gte=start_datetime,
            timestamp__lte=end_datetime
        )

        # Si no hay gastos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay gastos para el usuario {request.user} con los filtros aplicados")
            return Response({
                'labels': [],
                'datasets': [{
                    'label': 'Total de gastos',
                    'data': [],
                    'backgroundColor': 'rgba(75, 192, 192, 0.2)',
                    'borderColor': 'rgba(75, 192, 192, 1)',
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
        def get_time_key(timestamp, group):
            # Convertir de UTC a zona horaria del usuario para agrupación correcta
            local_datetime = timestamp.astimezone(user_timezone)
            date = local_datetime.date()

            if group == 'hour':
                # Redondear a la hora
                return datetime(date.year, date.month, date.day, local_datetime.hour, 0, 0)
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

        # Generar períodos de tiempo para el rango de fechas y diccionario para acumular gastos
        time_totals = {}

        if group_by == 'hour' and (end_date - start_date).days <= 1:
            # Horas para hoy o ayer (solo si es un día)
            start_datetime_local = datetime.combine(
                start_date, datetime.min.time())
            end_datetime_local = datetime.combine(
                end_date, datetime.max.time())
            current = start_datetime_local

            while current <= end_datetime_local:
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

        # Agrupar y sumar gastos por período de tiempo
        for expense in queryset:
            time_key = format_date_label(get_time_key(
                expense.timestamp, group_by), group_by)
            if time_key in time_totals:
                time_totals[time_key] += float(expense.amount)

        # Convertir el diccionario de totales a lista manteniendo el orden de las etiquetas
        for label in time_labels:
            totals_data.append(round(time_totals[label], 2))

        # Resumen del filtro aplicado
        filter_summary = "Gastos del año"
        if date_filter != 'all' and start_date and end_date:
            filter_summary = f"Gastos del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"

        # Formato final para Chart.js (una sola línea con los totales)
        response_data = {
            'labels': time_labels,
            'datasets': [{
                'label': 'Total de gastos',
                'data': totals_data,
                'backgroundColor': 'rgba(75, 192, 192, 0.2)',
                'borderColor': 'rgba(75, 192, 192, 1)',
                'borderWidth': 2,
                'fill': True,
                'tension': 0.1
            }],
            'filter_summary': filter_summary,
            'total_amount': round(sum(totals_data), 2)
        }

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def stacked_bar_chart_data(self, request):
        """
        Obtiene datos para una gráfica de barras apiladas de gastos por categoría y tiempo.

        GET /api/expenses/stacked_bar_chart_data/

        Parámetros:
        - category_id: ID de la categoría (opcional, si no se proporciona, se incluyen todas las categorías)
        - date_filter: Filtro de fecha (opcional, valores: 'all', 'today', 'yesterday', 'current_month', 'previous_month', 
                     'current_week', 'previous_week', 'current_year', 'previous_year', 'custom')
        - start_date: Fecha de inicio para filtro personalizado (formato: YYYY-MM-DD)
        - end_date: Fecha de fin para filtro personalizado (formato: YYYY-MM-DD)
        - limit: Número máximo de categorías a mostrar (opcional, por defecto muestra todas)
        - group_by: Agrupación temporal (opciones: 'day', 'week', 'month', por defecto ajusta automáticamente)
        - timezone: Zona horaria del usuario (opcional, por defecto 'America/Bogota' o UTC-5)
        """
        logger.info(
            f"Endpoint /stacked_bar_chart_data/ accedido por usuario: {request.user}")

        # Obtener queryset base (filtrado por usuario)
        queryset = self.get_queryset()

        # Obtener zona horaria del usuario
        user_timezone = self._get_user_timezone(request)
        # Obtener fecha/hora actual en la zona horaria del usuario
        local_now = self._get_local_datetime(request)
        today = local_now.date()

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

        if date_filter == 'today':
            # Hoy (en la zona horaria del usuario)
            start_date = today
            end_date = today
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'yesterday':
            # Ayer (en la zona horaria del usuario)
            start_date = today - timedelta(days=1)
            end_date = start_date
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'current_month':
            # Mes actual (en la zona horaria del usuario)
            start_date = today.replace(day=1)
            if today.month == 12:
                next_month = 1
                next_month_year = today.year + 1
            else:
                next_month = today.month + 1
                next_month_year = today.year

            end_date = datetime(next_month_year, next_month,
                                1).date() - timedelta(days=1)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'previous_month':
            # Mes anterior (en la zona horaria del usuario)
            first_day_current = today.replace(day=1)
            last_day_previous = first_day_current - timedelta(days=1)
            start_date = last_day_previous.replace(day=1)
            end_date = last_day_previous

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'current_week':
            # Semana actual (lunes a domingo en la zona horaria del usuario)
            start_date = today - timedelta(days=today.weekday())
            end_date = start_date + timedelta(days=6)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'previous_week':
            # Semana anterior (en la zona horaria del usuario)
            start_date = today - timedelta(days=today.weekday() + 7)
            end_date = start_date + timedelta(days=6)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'current_year':
            # Año actual (en la zona horaria del usuario)
            start_date = today.replace(month=1, day=1)
            end_date = today.replace(month=12, day=31)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'previous_year':
            # Año anterior (en la zona horaria del usuario)
            start_date = datetime(today.year - 1, 1, 1).date()
            end_date = datetime(today.year - 1, 12, 31).date()

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        elif date_filter == 'custom':
            # Filtro personalizado (considerando zona horaria del usuario)
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

                # Convertir a UTC para filtrar correctamente
                start_datetime = self._convert_to_utc(
                    start_date, False, user_timezone)
                end_datetime = self._convert_to_utc(
                    end_date, True, user_timezone)

            except (ValueError, TypeError):
                return Response(
                    {"error": "Formato de fecha inválido. Use YYYY-MM-DD"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            # 'all' o cualquier otro valor: usar último año
            end_date = today
            start_date = end_date - timedelta(days=365)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

        # Aplicar filtro de fecha al queryset
        queryset = queryset.filter(
            timestamp__gte=start_datetime,
            timestamp__lte=end_datetime
        )

        # Si no hay gastos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay gastos para el usuario {request.user} con los filtros aplicados")
            return Response({
                'labels': [],
                'datasets': []
            })

        # Determinar agrupación temporal según el rango de fecha
        group_by = request.query_params.get('group_by')
        if not group_by:
            # Ajustar agrupación según duración del periodo
            days_diff = (end_date - start_date).days
            if days_diff <= 1:
                group_by = 'hour'
            elif days_diff <= 31:
                group_by = 'day'
            elif days_diff <= 365:
                group_by = 'week'
            else:
                group_by = 'month'

        # Definir función para obtener la clave de agrupación temporal
        def get_time_key(timestamp, group):
            # Convertir de UTC a zona horaria del usuario para agrupación correcta
            local_datetime = timestamp.astimezone(user_timezone)
            date = local_datetime.date()

            if group == 'hour':
                return datetime(date.year, date.month, date.day, local_datetime.hour, 0, 0)
            elif group == 'day':
                return date
            elif group == 'week':
                # Lunes de la semana
                week_start = date - timedelta(days=date.weekday())
                return week_start
            else:  # 'month' (por defecto)
                return date.replace(day=1)

        # Definir función para formatear la etiqueta de tiempo
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

        # Generar períodos de tiempo para el rango de fechas
        time_periods = []
        time_labels = []

        if group_by == 'hour' and (end_date - start_date).days <= 1:
            # Horas para hoy o ayer (solo si es un día)
            start_datetime_local = datetime.combine(
                start_date, datetime.min.time())
            end_datetime_local = datetime.combine(
                end_date, datetime.max.time())
            current = start_datetime_local

            while current <= end_datetime_local:
                time_periods.append(current)
                time_labels.append(format_date_label(current, group_by))
                current += timedelta(hours=1)

        elif group_by == 'day':
            current = start_date
            while current <= end_date:
                time_periods.append(current)
                time_labels.append(format_date_label(current, group_by))
                current += timedelta(days=1)

        elif group_by == 'week':
            # Ajustar a lunes
            current = start_date - timedelta(days=start_date.weekday())
            while current <= end_date:
                time_periods.append(current)
                time_labels.append(format_date_label(current, group_by))
                current += timedelta(days=7)

        else:  # 'month' (por defecto)
            current = start_date.replace(day=1)
            while current <= end_date:
                time_periods.append(current)
                time_labels.append(format_date_label(current, group_by))

                # Avanzar al siguiente mes
                if current.month == 12:
                    current = datetime(current.year + 1, 1, 1).date()
                else:
                    current = datetime(
                        current.year, current.month + 1, 1).date()

        # Obtener todas las categorías con gastos en el período
        categories = {}
        for expense in queryset:
            category_name = expense.category.name if expense.category else 'Otros'
            if category_name == '':
                category_name = 'Otros'

            if category_name not in categories:
                category = expense.category
                color = category.color if category else '#CCCCCC'
                categories[category_name] = {
                    'name': category_name,
                    'color': color
                }

        # Limitar el número de categorías si se solicita
        limit = request.query_params.get('limit')
        if limit:
            try:
                limit = int(limit)
                # Obtener las categorías con mayores gastos
                category_totals = {}
                for expense in queryset:
                    category_name = expense.category.name if expense.category else 'Otros'
                    if category_name == '':
                        category_name = 'Otros'

                    if category_name not in category_totals:
                        category_totals[category_name] = 0
                    category_totals[category_name] += float(expense.amount)

                # Ordenar categorías por total y limitar
                top_categories = sorted(category_totals.items(
                ), key=lambda x: x[1], reverse=True)[:limit]
                top_category_names = [item[0] for item in top_categories]

                # Filtrar las categorías seleccionadas
                categories = {
                    name: cat for name, cat in categories.items() if name in top_category_names}
            except ValueError:
                pass  # Ignorar si no es un entero válido

        # Crear estructura para agrupar gastos por período y categoría
        time_category_data = {}
        for period in time_periods:
            period_label = format_date_label(period, group_by)
            time_category_data[period_label] = {
                cat: 0 for cat in categories.keys()}

        # Clasificar cada gasto en su período y categoría correspondiente
        for expense in queryset:
            # Obtener período
            time_key = get_time_key(expense.timestamp, group_by)
            period_label = format_date_label(time_key, group_by)

            # Obtener categoría
            category_name = expense.category.name if expense.category else 'Otros'
            if category_name == '':
                category_name = 'Otros'

            # Asignar solo si la categoría está en las seleccionadas
            if period_label in time_category_data and category_name in categories:
                time_category_data[period_label][category_name] += float(
                    expense.amount)

        # Preparar datasets para Chart.js (formato para barras apiladas)
        datasets = []
        for category_name, category_info in categories.items():
            data = []
            for label in time_labels:
                if label in time_category_data:
                    data.append(
                        round(time_category_data[label][category_name], 2))
                else:
                    data.append(0)

            datasets.append({
                'label': category_name,
                'data': data,
                'backgroundColor': category_info['color'],
                'borderColor': category_info['color'],
                'borderWidth': 1
            })

        # Calcular total general
        total_amount = 0
        for expense in queryset:
            category_name = expense.category.name if expense.category else 'Otros'
            if category_name == '':
                category_name = 'Otros'

            if category_name in categories:
                total_amount += float(expense.amount)

        # Resumen del filtro aplicado
        filter_summary = "Todos los gastos"
        if date_filter != 'all' and start_date and end_date:
            filter_summary = f"Gastos del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"

        # Formato final para Chart.js (stacked bar chart)
        response_data = {
            'labels': time_labels,
            'datasets': datasets,
            'filter_summary': filter_summary,
            'total_amount': round(total_amount, 2),
            'group_by': group_by,
            'recent_expenses': self.get_serializer(
                queryset.order_by('-timestamp')[:10],
                many=True
            ).data
        }

        return Response(response_data)
