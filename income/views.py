from django.shortcuts import render
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum
from datetime import datetime, timedelta
from django.utils import timezone
import logging
import pytz

from .models import Income, IncomeCategory
from .serializers import IncomeSerializer, IncomeCategorySerializer
# Importar utilidades para categorías por usuario
from categories.models import UserIncomeCategory
from categories.utils import get_user_income_categories_queryset

# Configurar logger
logger = logging.getLogger(__name__)

# Zona horaria predeterminada (UTC-5)
DEFAULT_TIMEZONE = pytz.timezone('America/Bogota')  # Equivalente a UTC-5


def get_income_category_name(income):
    """
    Función auxiliar para obtener el nombre de la categoría de un ingreso
    priorizando user_income_category sobre category legacy
    """
    if income.user_income_category:
        return income.user_income_category.name
    elif income.category:
        return income.category.name
    elif hasattr(income, 'category_str') and income.category_str:
        return income.category_str
    else:
        return 'Sin categoría'


class IncomeCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = IncomeCategorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Obtener categorías globales (legacy) - Deprecado, usar UserIncomeCategoryViewSet"""
        # Este ViewSet se mantiene para compatibilidad legacy
        return IncomeCategory.objects.all()

    def perform_create(self, serializer):
        """Sobrescribe el método para asegurar que se guarden los nuevos campos"""
        serializer.save()

    @action(detail=False, methods=['get'])
    def with_details(self, request):
        """
        Devuelve todas las categorías con sus detalles completos (descripción y ejemplos).
        DEPRECADO: Usar las nuevas APIs de categorías por usuario en /api/categories/

        GET /api/income-categories/with_details/
        """
        logger.warning(
            "Endpoint legacy /with_details/ usado - considerar migrar a categorías por usuario")

        # Priorizar categorías por usuario
        user_categories = get_user_income_categories_queryset(request.user)

        data = []
        for category in user_categories:
            data.append({
                'id': category.id,
                'name': category.name,
                'color': category.color,
                'description': category.description or '',
                'example': category.example or '',
                'is_default': category.is_default,
                'type': 'user_category'
            })

        # Si no hay categorías por usuario, usar globales como fallback
        if not data:
            logger.warning(
                f"No hay categorías por usuario para {request.user}, usando globales")
            global_categories = self.get_queryset()
            for category in global_categories:
                data.append({
                    'id': category.id,
                    'name': category.name,
                    'color': category.color,
                    'description': category.description or '',
                    'example': category.example or '',
                    'is_default': False,
                    'type': 'global_category'
                })

        return Response(data)

    @action(detail=False, methods=['get'])
    def colors_map(self, request):
        """
        Devuelve un mapa de nombre de categoría -> color para usar en visualizaciones.
        Actualizado para usar categorías por usuario.

        GET /api/income-categories/colors_map/
        """
        # Priorizar categorías por usuario
        user_categories = get_user_income_categories_queryset(request.user)

        colors_map = {}
        for category in user_categories:
            colors_map[category.name] = category.color

        # Si no hay categorías por usuario, usar globales como fallback
        if not colors_map:
            global_categories = self.get_queryset()
            colors_map = {
                category.name: category.color
                for category in global_categories
            }

        return Response(colors_map)

    @action(detail=True, methods=['put', 'patch'])
    def update_details(self, request, pk=None):
        """
        Actualiza los detalles específicos de una categoría (descripción, ejemplo, color).
        DEPRECADO: Usar las nuevas APIs de categorías por usuario en /api/categories/

        PUT/PATCH /api/income-categories/{id}/update_details/

        Parámetros:
        - description: Descripción de la categoría
        - example: Ejemplos de ingresos para esta categoría
        - color: Color hexadecimal (formato: #RRGGBB)
        """
        logger.warning(
            "Endpoint legacy /update_details/ usado - considerar migrar a categorías por usuario")

        try:
            category = self.get_object()

            # Actualizar campos si están presentes en la solicitud
            if 'description' in request.data:
                category.description = request.data['description']

            if 'example' in request.data:
                category.example = request.data['example']

            if 'color' in request.data:
                category.color = request.data['color']

            category.save()

            serializer = self.get_serializer(category)
            return Response(serializer.data)
        except Exception as e:
            return Response(
                {"error": f"Error al actualizar la categoría: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )


class IncomeViewSet(viewsets.ModelViewSet):
    serializer_class = IncomeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return Income.objects.filter(user=user).order_by('-timestamp')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def _get_user_timezone(self, request):
        """Obtiene la zona horaria del usuario con el siguiente orden de prioridad:
        1. Parámetro de la petición (temporal, sobrescribe las otras)
        2. Configuración guardada del usuario
        3. Valor por defecto (America/Bogota)
        """
        # Prioridad 1: Parámetro en la petición
        tz_param = request.query_params.get('timezone')
        if tz_param:
            try:
                # Intenta interpretar como nombre de zona horaria (ej. 'America/Bogota')
                user_timezone = pytz.timezone(tz_param)
                return user_timezone
            except pytz.exceptions.UnknownTimeZoneError:
                try:
                    # Intenta interpretar como offset numérico (ej. '-5')
                    offset_hours = int(tz_param)
                    offset = timedelta(hours=offset_hours)
                    # Crear zona horaria con el offset
                    user_timezone = pytz.FixedOffset(
                        offset.total_seconds() // 60)
                    return user_timezone
                except ValueError:
                    # Si hay error, continuar con la siguiente prioridad
                    pass

        # Prioridad 2: Zona horaria guardada del usuario
        if request.user and request.user.is_authenticated:
            try:
                user_timezone = pytz.timezone(request.user.timezone)
                return user_timezone
            except (pytz.exceptions.UnknownTimeZoneError, AttributeError):
                # Si hay error o el usuario no tiene timezone configurado, continuar
                pass

        # Valor por defecto
        return DEFAULT_TIMEZONE

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
    def summary(self, request):
        """Obtiene un resumen de ingresos por período usando categorías por usuario"""
        user = request.user
        period = request.query_params.get('period', 'month')

        # Obtener zona horaria del usuario
        user_timezone = self._get_user_timezone(request)
        # Obtener fecha/hora actual en la zona horaria del usuario
        local_now = self._get_local_datetime(request)
        today = local_now.date()

        # Determinar fecha de inicio según el período
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
            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            queryset = queryset.filter(timestamp__gte=start_datetime)

        # Calcular totales por categoría incluyendo el ID, priorizando categorías por usuario
        summary = queryset.values(
            'id',
            'user_income_category__name',
            'category__name',
            'currency'
        ).annotate(
            total=Sum('amount')
        ).order_by('-total')

        # Transformar para incluir nombre de categoría correcto
        transformed_summary = []
        for item in summary:
            # Priorizar categoría por usuario, fallback a categoría global
            category_name = item['user_income_category__name'] or item['category__name'] or 'Otros'
            transformed_summary.append({
                'id': item['id'],
                'category__name': category_name,
                'currency': item['currency'],
                'total': item['total']
            })

        # Calcular total general
        total = queryset.aggregate(Sum('amount'))['amount__sum'] or 0

        return Response({
            'period': period,
            'start_date': start_date,
            'end_date': today,
            'summary': transformed_summary,
            'total': total
        })

    @action(detail=False, methods=['get'])
    def donut_chart_data(self, request):
        """Obtiene ingresos filtrados por categoría y rango de fecha, con formato para gráfica de dona.

        GET /api/income/donut_chart_data/

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
            # 'all' o cualquier otro valor: usar último año
            end_date = today
            start_date = end_date - timedelta(days=365)

            # Convertir a UTC para filtrar correctamente
            start_datetime = self._convert_to_utc(
                start_date, False, user_timezone)
            end_datetime = self._convert_to_utc(end_date, True, user_timezone)

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
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

        # Agrupar por categoría y calcular totales, priorizando categorías por usuario
        by_category = queryset.values('user_income_category__name', 'category__name').annotate(
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
        backgroundColor = []
        hoverBackgroundColor = []
        category_details = []  # Agregar información detallada de categorías

        for item in by_category:
            # Priorizar categoría por usuario, fallback a categoría global
            category_name = item['user_income_category__name'] or item['category__name'] or 'Sin categoría'

            # Buscar primero en categorías por usuario
            user_category = None
            if item['user_income_category__name']:
                user_category = UserIncomeCategory.objects.filter(
                    user=request.user,
                    name=item['user_income_category__name']
                ).first()

            # Si no se encuentra en categorías por usuario, buscar en globales
            if user_category:
                color = user_category.color
                description = user_category.description or ''
                example = user_category.example or ''
            else:
                global_category = IncomeCategory.objects.filter(
                    name=category_name).first()
                color = global_category.color if global_category and hasattr(
                    global_category, 'color') else '#4CAF50'
                description = global_category.description if global_category else ''
                example = global_category.example if global_category else ''

            # Incluir información adicional de la categoría
            category_info = {
                'name': category_name,
                'color': color,
                'total': float(item['total']),
                'description': description,
                'example': example
            }

            category_details.append(category_info)

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
            'recent_incomes': incomes,  # Incluir algunos ingresos recientes para detalles
            # Nueva información detallada de categorías
            'category_details': category_details
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

            queryset = queryset.filter(
                timestamp__gte=start_datetime,
                timestamp__lte=end_datetime
            )

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

        # Agrupar por categoría y calcular totales, priorizando categorías por usuario
        by_category = queryset.values('user_income_category__name', 'category__name').annotate(
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
        category_details = []  # Agregar información detallada de categorías

        for item in by_category:
            # Priorizar categoría por usuario, fallback a categoría global
            category_name = item['user_income_category__name'] or item['category__name'] or 'Sin categoría'

            # Buscar primero en categorías por usuario
            user_category = None
            if item['user_income_category__name']:
                user_category = UserIncomeCategory.objects.filter(
                    user=request.user,
                    name=item['user_income_category__name']
                ).first()

            # Si no se encuentra en categorías por usuario, buscar en globales
            if user_category:
                color = user_category.color
                description = user_category.description or ''
                example = user_category.example or ''
            else:
                global_category = IncomeCategory.objects.filter(
                    name=category_name).first()
                color = global_category.color if global_category and hasattr(
                    global_category, 'color') else '#4CAF50'
                description = global_category.description if global_category else ''
                example = global_category.example if global_category else ''

            # Incluir información adicional de la categoría
            category_info = {
                'name': category_name,
                'color': color,
                'total': float(item['total']),
                'description': description,
                'example': example
            }

            category_details.append(category_info)

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
            ).data,
            # Nueva información detallada de categorías
            'category_details': category_details
        }

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def line_chart_data(self, request):
        """
        Obtiene datos para una gráfica de línea que muestra el total de ingresos a lo largo del tiempo.

        GET /api/incomes/line_chart_data/

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

        # Si no hay ingresos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay ingresos para el usuario {request.user} con los filtros aplicados")
            return Response({
                'labels': [],
                'datasets': [{
                    'label': 'Total de ingresos',
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
        Obtiene datos para una gráfica de barras apiladas de ingresos por categoría y tiempo.

        GET /api/income/stacked_bar_chart_data/

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

        # Si no hay ingresos, devolver respuesta vacía
        if not queryset.exists():
            logger.warning(
                f"No hay ingresos para el usuario {request.user} con los filtros aplicados")
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

        # Obtener todas las categorías con ingresos en el período
        categories = {}
        for income in queryset:
            category_name = get_income_category_name(income)
            if category_name not in categories:
                # Obtener información de categoría priorizando user_income_category
                user_category = income.user_income_category
                legacy_category = income.category

                if user_category:
                    color = user_category.color
                elif legacy_category and hasattr(legacy_category, 'color'):
                    color = legacy_category.color
                else:
                    color = '#4CAF50'  # Verde por defecto para ingresos

                categories[category_name] = {
                    'name': category_name,
                    'color': color
                }

        # Limitar el número de categorías si se solicita
        limit = request.query_params.get('limit')
        if limit:
            try:
                limit = int(limit)
                # Obtener las categorías con mayores ingresos
                category_totals = {}
                for income in queryset:
                    category_name = get_income_category_name(income)
                    if category_name not in category_totals:
                        category_totals[category_name] = 0
                    category_totals[category_name] += float(income.amount)

                # Ordenar categorías por total y limitar
                top_categories = sorted(category_totals.items(
                ), key=lambda x: x[1], reverse=True)[:limit]
                top_category_names = [item[0] for item in top_categories]

                # Filtrar las categorías seleccionadas
                categories = {
                    name: cat for name, cat in categories.items() if name in top_category_names}
            except ValueError:
                pass  # Ignorar si no es un entero válido

        # Crear estructura para agrupar ingresos por período y categoría
        time_category_data = {}
        for period in time_periods:
            period_label = format_date_label(period, group_by)
            time_category_data[period_label] = {
                cat: 0 for cat in categories.keys()}

        # Clasificar cada ingreso en su período y categoría correspondiente
        for income in queryset:
            # Obtener período
            time_key = get_time_key(income.timestamp, group_by)
            period_label = format_date_label(time_key, group_by)

            # Obtener categoría
            category_name = get_income_category_name(income)

            # Asignar solo si la categoría está en las seleccionadas
            if period_label in time_category_data and category_name in categories:
                time_category_data[period_label][category_name] += float(
                    income.amount)

        # Preparar datasets para Chart.js (formato para barras apiladas)
        datasets = []
        category_metadata = {}  # Metadata adicional para cada categoría

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

            # Obtener descripción y ejemplos para la categoría
            try:
                category_obj = IncomeCategory.objects.get(name=category_name)
                category_metadata[category_name] = {
                    'description': category_obj.description or '',
                    'example': category_obj.example or '',
                    'color': category_obj.color
                }
            except IncomeCategory.DoesNotExist:
                category_metadata[category_name] = {
                    'description': '',
                    'example': '',
                    'color': category_info['color']
                }

        # Calcular total general
        total_amount = 0
        for income in queryset:
            category_name = get_income_category_name(income)
            if category_name in categories:
                total_amount += float(income.amount)

        # Resumen del filtro aplicado
        filter_summary = "Todos los ingresos"
        if date_filter != 'all' and start_date and end_date:
            filter_summary = f"Ingresos del {start_date.strftime('%d/%m/%Y')} al {end_date.strftime('%d/%m/%Y')}"

        # Formato final para Chart.js (stacked bar chart)
        response_data = {
            'labels': time_labels,
            'datasets': datasets,
            'filter_summary': filter_summary,
            'total_amount': round(total_amount, 2),
            'group_by': group_by,
            'recent_incomes': self.get_serializer(
                queryset.order_by('-timestamp')[:10],
                many=True
            ).data,
            'category_metadata': category_metadata  # Incluir metadata de categorías
        }

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """
        Obtiene estadísticas de ingresos incluyendo promedio mensual, comparación
        con el mes anterior y proyección para el próximo mes.

        GET /api/incomes/statistics/

        Parámetros:
        - months_for_average: Número de meses para calcular el promedio (por defecto: 3)
        - timezone: Zona horaria del usuario (opcional)
        """
        logger.info(
            f"Endpoint /statistics/ accedido por usuario: {request.user}")

        # Obtener zona horaria del usuario
        user_timezone = self._get_user_timezone(request)
        # Obtener fecha/hora actual en la zona horaria del usuario
        local_now = self._get_local_datetime(request)
        today = local_now.date()

        # Número de meses para calcular el promedio
        try:
            months_for_average = int(
                request.query_params.get('months_for_average', 3))
            if months_for_average <= 0:
                months_for_average = 3
        except ValueError:
            months_for_average = 3

        # Obtener queryset base (filtrado por usuario)
        queryset = self.get_queryset()

        # Calcular inicio y fin del mes actual
        first_day_current_month = today.replace(day=1)
        if today.month == 12:
            next_month = 1
            next_month_year = today.year + 1
        else:
            next_month = today.month + 1
            next_month_year = today.year
        last_day_current_month = datetime(
            next_month_year, next_month, 1).date() - timedelta(days=1)

        # Convertir a UTC para filtrar correctamente
        start_current_month = self._convert_to_utc(
            first_day_current_month, False, user_timezone)
        end_current_month = self._convert_to_utc(
            last_day_current_month, True, user_timezone)

        # Filtrar ingresos del mes actual
        current_month_income = queryset.filter(
            timestamp__gte=start_current_month,
            timestamp__lte=end_current_month
        ).aggregate(total=Sum('amount'))['total'] or 0

        # Calcular inicio y fin del mes anterior
        last_day_previous_month = first_day_current_month - timedelta(days=1)
        first_day_previous_month = last_day_previous_month.replace(day=1)

        # Convertir a UTC para filtrar correctamente
        start_previous_month = self._convert_to_utc(
            first_day_previous_month, False, user_timezone)
        end_previous_month = self._convert_to_utc(
            last_day_previous_month, True, user_timezone)

        # Filtrar ingresos del mes anterior
        previous_month_income = queryset.filter(
            timestamp__gte=start_previous_month,
            timestamp__lte=end_previous_month
        ).aggregate(total=Sum('amount'))['total'] or 0

        # Calcular diferencia con el mes anterior
        difference = current_month_income - previous_month_income

        # Calcular porcentaje de cambio
        if previous_month_income > 0:
            percentage_change = (difference / previous_month_income) * 100
        else:
            percentage_change = 100 if current_month_income > 0 else 0

        # Calcular promedio de los últimos X meses
        monthly_average = 0
        monthly_totals = []

        # Recorrer los últimos X meses para calcular el promedio
        current_start = first_day_previous_month
        for i in range(months_for_average):
            month_end = current_start - timedelta(days=1)
            month_start = month_end.replace(day=1)

            # Convertir a UTC
            month_start_utc = self._convert_to_utc(
                month_start, False, user_timezone)
            month_end_utc = self._convert_to_utc(
                month_end, True, user_timezone)

            # Obtener total del mes
            month_total = queryset.filter(
                timestamp__gte=month_start_utc,
                timestamp__lte=month_end_utc
            ).aggregate(total=Sum('amount'))['total'] or 0

            monthly_totals.append(month_total)
            current_start = month_start

        # Incluir el mes actual en el promedio
        monthly_totals.append(current_month_income)

        # Calcular promedio si hay datos
        if monthly_totals:
            monthly_average = sum(monthly_totals) / len(monthly_totals)

        # Proyección para el próximo mes
        # Usamos una simple tendencia lineal basada en el mes actual y anterior
        if previous_month_income > 0 and current_month_income > 0:
            # Si ambos meses tienen datos, proyectamos con la tendencia
            growth_rate = current_month_income / previous_month_income
            next_month_projection = current_month_income * growth_rate
        else:
            # Si no hay suficientes datos, usamos el promedio
            next_month_projection = monthly_average

        # Preparar respuesta
        response_data = {
            'average_monthly_income': round(monthly_average, 2),
            'current_month_income': round(current_month_income, 2),
            'previous_month_income': round(previous_month_income, 2),
            'difference': round(difference, 2),
            'percentage_change': round(percentage_change, 2),
            'next_month_projection': round(next_month_projection, 2),
            'months_analyzed': months_for_average + 1  # +1 por incluir el mes actual
        }

        return Response(response_data)

    def destroy(self, request, *args, **kwargs):
        """
        Elimina un ingreso específico.
        DELETE /api/incomes/{id}/

        Respuestas:
        - 200 OK: Ingreso eliminado exitosamente
        - 400 Bad Request: Error al eliminar el ingreso
        - 403 Forbidden: No tienes permiso para eliminar este ingreso
        - 404 Not Found: Ingreso no encontrado
        """
        income_id = kwargs.get('pk')
        logger.info(f"Intentando eliminar ingreso con ID: {income_id}")

        try:
            # Verificar si el ingreso existe
            try:
                instance = Income.objects.get(id=income_id)
                logger.info(f"Ingreso encontrado: {instance.id}")
            except Income.DoesNotExist:
                logger.warning(
                    f"No se encontró el ingreso con ID: {income_id}")
                return Response(
                    {"error": f"No existe un ingreso con el ID {income_id}"},
                    status=status.HTTP_404_NOT_FOUND
                )

            # Verificar que el ingreso pertenece al usuario actual
            if instance.user != request.user:
                logger.warning(
                    f"Usuario {request.user} intentando eliminar ingreso {instance.id} que no le pertenece")
                return Response(
                    {"error": "No tienes permiso para eliminar este ingreso"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Guardar información del ingreso antes de eliminarlo para el log
            income_info = {
                'id': instance.id,
                'amount': instance.amount,
                'currency': instance.currency,
                'category': get_income_category_name(instance),
                'description': instance.description
            }

            logger.info(
                f"Usuario {request.user} eliminando ingreso: {income_info}")

            # Eliminar el ingreso
            instance.delete()
            logger.info(f"Ingreso {income_id} eliminado exitosamente")

            return Response({
                "message": "Ingreso eliminado exitosamente",
                "deleted_income": income_info
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error al eliminar ingreso {income_id}: {str(e)}")
            return Response(
                {"error": f"Error al eliminar el ingreso: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
