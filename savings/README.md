# 💰 Módulo de Ahorros - CashBot API

## Descripción

El módulo de ahorros es una funcionalidad completa para la gestión de metas de ahorro personal. Como financiero experto, he diseñado este módulo con las mejores prácticas financieras y características avanzadas para ayudar a los usuarios a alcanzar sus objetivos de ahorro.

## 🎯 Características Principales

### 📊 Gestión de Metas de Ahorro

- **Creación de metas personalizadas** con montos objetivo y fechas límite
- **Seguimiento de progreso** con cálculos automáticos de porcentajes
- **Estados de metas**: Activa, Completada, Pausada, Cancelada
- **Prioridades**: Urgente, Alta, Media, Baja
- **Ahorro automático** configurable (diario, semanal, quincenal, mensual)

### 🏷️ Categorías Inteligentes

- **10 categorías predefinidas** basadas en principios financieros
- **Colores e íconos** para una mejor organización visual
- **Información adicional** con descripciones y ejemplos

### 💵 Gestión de Depósitos

- **Múltiples tipos de transacciones**: Manual, Automático, Transferencia, Retiro, Interés, Bono
- **Historial completo** de todos los movimientos
- **Validaciones** para evitar sobregiros
- **Metadatos** como fuente del depósito y notas

### 📋 Plantillas de Ahorro

- **10 plantillas predefinidas** para metas comunes
- **Rangos de montos sugeridos** basados en estándares financieros
- **Plazos recomendados** y consejos expertos
- **Creación rápida** de metas desde plantillas

### 📈 Analytics y Reportes

- **Resumen completo** de todas las metas
- **Análisis de rendimiento** de ahorro
- **Gráficos de progreso temporal** por meta
- **Recomendaciones personalizadas** basadas en el comportamiento

## 🛠️ Modelos de Datos

### SavingsCategory

Categorías para organizar las metas de ahorro.

```python
- name: CharField (único)
- description: TextField
- color: CharField (código hex)
- icon: CharField
- is_active: BooleanField
```

### SavingsGoal

Meta principal de ahorro del usuario.

```python
- user: ForeignKey (User)
- category: ForeignKey (SavingsCategory)
- name: CharField
- description: TextField
- target_amount: DecimalField
- current_amount: DecimalField (calculado)
- currency: CharField (default: COP)
- target_date: DateField
- status: CharField (choices)
- priority: CharField (choices)
- auto_save_enabled: BooleanField
- auto_save_amount: DecimalField
- auto_save_frequency: CharField
```

### SavingsDeposit

Registro de todos los depósitos y retiros.

```python
- savings_goal: ForeignKey (SavingsGoal)
- amount: DecimalField (positivo = depósito, negativo = retiro)
- description: CharField
- transaction_type: CharField (choices)
- timestamp: DateTimeField
- source: CharField
- notes: TextField
```

### SavingsTemplate

Plantillas predefinidas para crear metas rápidamente.

```python
- name: CharField
- description: TextField
- category: ForeignKey (SavingsCategory)
- suggested_amount_min/max: DecimalField
- suggested_timeframe_months: IntegerField
- priority: CharField
- tips: TextField
```

## 🔌 Endpoints de API

### Categorías de Ahorro

```
GET    /api/savings/categories/                    # Listar categorías
GET    /api/savings/categories/with_goals_count/   # Categorías con conteo de metas
```

### Metas de Ahorro

```
GET    /api/savings/goals/                    # Listar metas del usuario
POST   /api/savings/goals/                    # Crear nueva meta
GET    /api/savings/goals/{id}/               # Detalle de meta específica
PUT    /api/savings/goals/{id}/               # Actualizar meta
DELETE /api/savings/goals/{id}/               # Eliminar meta

# Endpoints especializados
GET    /api/savings/goals/active/             # Solo metas activas
GET    /api/savings/goals/completed/          # Solo metas completadas
GET    /api/savings/goals/by_priority/        # Agrupadas por prioridad
GET    /api/savings/goals/summary/            # Resumen completo
GET    /api/savings/goals/analytics/          # Análisis avanzado
GET    /api/savings/goals/recommendations/    # Recomendaciones personalizadas

# Transacciones en metas
POST   /api/savings/goals/{id}/add_deposit/   # Agregar depósito
POST   /api/savings/goals/{id}/withdraw/      # Retirar dinero
GET    /api/savings/goals/{id}/progress_chart/ # Datos para gráfico de progreso
```

### Depósitos

```
GET    /api/savings/deposits/                 # Listar depósitos del usuario
GET    /api/savings/deposits/recent/          # Depósitos recientes
GET    /api/savings/deposits/by_goal/         # Filtrados por meta
```

### Plantillas

```
GET    /api/savings/templates/                # Listar plantillas disponibles
GET    /api/savings/templates/by_category/    # Agrupadas por categoría
POST   /api/savings/templates/{id}/create_goal_from_template/  # Crear meta desde plantilla
```

## 📝 Ejemplos de Uso

### 1. Crear una Nueva Meta de Ahorro

```bash
POST /api/savings/goals/
{
    "name": "Viaje a Europa 2024",
    "description": "Ahorro para vacaciones familiares en Europa",
    "category": 2,  # ID de categoría Vacaciones
    "target_amount": "8000000.00",
    "target_date": "2024-12-01",
    "priority": "medium",
    "auto_save_enabled": true,
    "auto_save_amount": "250000.00",
    "auto_save_frequency": "monthly"
}
```

### 2. Agregar un Depósito

```bash
POST /api/savings/goals/{goal_id}/add_deposit/
{
    "amount": "500000.00",
    "description": "Depósito mensual de octubre",
    "transaction_type": "manual",
    "source": "Salario"
}
```

### 3. Crear Meta desde Plantilla

```bash
POST /api/savings/templates/1/create_goal_from_template/
{
    "name": "Mi Fondo de Emergencia",
    "target_amount": "3000000.00",
    "target_date": "2024-06-01",
    "auto_save_enabled": true,
    "auto_save_amount": "250000.00",
    "auto_save_frequency": "monthly"
}
```

### 4. Obtener Resumen de Ahorros

```bash
GET /api/savings/goals/summary/
```

**Respuesta:**

```json
{
    "total_saved": 2500000.00,
    "total_target": 15000000.00,
    "overall_progress": 16.67,
    "active_goals_count": 3,
    "completed_goals_count": 1,
    "paused_goals_count": 0,
    "goals_by_priority": {
        "urgent": {"label": "Urgente", "count": 1},
        "high": {"label": "Alta", "count": 2},
        "medium": {"label": "Media", "count": 0},
        "low": {"label": "Baja", "count": 0}
    },
    "top_categories": [...],
    "upcoming_deadlines": [...]
}
```

### 5. Obtener Recomendaciones Personalizadas

```bash
GET /api/savings/goals/recommendations/
```

**Respuesta:**

```json
{
  "available_for_savings": 1200000.0,
  "monthly_income": 3500000.0,
  "monthly_expenses": 2300000.0,
  "recommendations": [
    {
      "type": "emergency_fund",
      "title": "Crear Fondo de Emergencia",
      "description": "Se recomienda tener un fondo de emergencia equivalente a 6 meses de gastos ($13,800,000.00)",
      "suggested_amount": 13800000.0,
      "priority": "urgent",
      "category": "Fondo de Emergencia"
    }
  ]
}
```

## 💡 Consejos Financieros Implementados

### 1. **Regla 50/30/20**

- 50% para necesidades básicas
- 30% para deseos
- 20% para ahorros e inversiones

### 2. **Fondo de Emergencia Prioritario**

- Primera meta financiera obligatoria
- 3-6 meses de gastos básicos
- Acceso inmediato y sin riesgo

### 3. **Diversificación de Metas**

- Múltiples objetivos con diferentes prioridades
- Balance entre corto, mediano y largo plazo
- Categorización inteligente

### 4. **Ahorro Automático**

- Configuración de depósitos recurrentes
- "Pagar primero a ti mismo"
- Facilita la consistencia

## 🔧 Comandos de Gestión

```bash
# Crear datos iniciales (categorías y plantillas)
python manage.py create_savings_data

# Crear migraciones
python manage.py makemigrations savings

# Aplicar migraciones
python manage.py migrate savings
```

## 🎨 Categorías Predefinidas

1. **Fondo de Emergencia** 🚨 (#FF6B6B) - Reserva para emergencias
2. **Vacaciones** ✈️ (#4ECDC4) - Ahorro para viajes
3. **Compra de Casa** 🏠 (#45B7D1) - Ahorro para vivienda
4. **Compra de Vehículo** 🚗 (#96CEB4) - Ahorro para automóvil
5. **Educación** 📚 (#FFEAA7) - Estudios y capacitaciones
6. **Jubilación** 👴 (#DDA0DD) - Ahorro para el retiro
7. **Inversiones** 📈 (#74B9FF) - Capital para inversiones
8. **Salud** 🏥 (#55A3FF) - Gastos médicos
9. **Tecnología** 💻 (#636E72) - Equipos tecnológicos
10. **Eventos Especiales** 🎉 (#FD79A8) - Bodas, celebraciones

## 🚀 Funcionalidades Avanzadas

### Cálculos Automáticos

- **Progreso en porcentaje**: `(current_amount / target_amount) * 100`
- **Monto restante**: `target_amount - current_amount`
- **Días hasta la meta**: Diferencia entre fecha actual y objetivo
- **Ahorro diario recomendado**: `remaining_amount / days_remaining`

### Validaciones Inteligentes

- Evita retiros mayores al saldo disponible
- Valida fechas objetivo futuras
- Requiere configuración completa para ahorro automático
- Límites mínimos y máximos en montos

### Integración con Otros Módulos

- Análisis de ingresos vs gastos para recomendaciones
- Sugerencias basadas en el comportamiento financiero
- Cálculo de disponibilidad para ahorros

## 📊 Métricas y KPIs

- **Tasa de ahorro**: Porcentaje de ingresos destinado a ahorros
- **Velocidad de progreso**: Qué tan rápido se alcanzan las metas
- **Distribución por categorías**: Balance entre tipos de objetivos
- **Consistencia**: Frecuencia de depósitos
- **Completion rate**: Porcentaje de metas completadas exitosamente

---

Este módulo representa una solución completa y profesional para la gestión de ahorros personales, incorporando las mejores prácticas financieras y una experiencia de usuario excepcional. 🎯💰
