# 📊 Módulo de Ahorros - Documentación API Completa

## 🎯 Resumen General

El módulo de ahorros de CashBot permite a los usuarios crear y gestionar metas de ahorro personalizadas con funcionalidades avanzadas como:

- **Categorías personalizadas por usuario** con colores e íconos
- **Metas de ahorro inteligentes** con cálculos automáticos
- **Plantillas expertas** basadas en principios financieros
- **Depósitos y retiros** con seguimiento detallado
- **Analytics y recomendaciones** personalizadas
- **Ahorro automático** configurable por frecuencia

---

## 🏗️ Modelos de Datos

### 1. SavingsCategory

Categorías para organizar las metas de ahorro (específicas por usuario).

```javascript
{
  "id": "uuid",
  "user": "user_id",
  "name": "string(100)",
  "description": "string(500)",
  "color": "string(7)",      // Formato hexadecimal #RRGGBB
  "icon": "string(50)",      // Nombre del ícono
  "is_default": "boolean",   // true para categorías predefinidas
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

**Categorías Predefinidas:**

- 🚨 Emergencia (#FF5722)
- 🏖️ Vacaciones (#00BCD4)
- 🏠 Casa (#4CAF50)
- 🚗 Vehículo (#9C27B0)
- 📚 Educación (#3F51B5)
- 💍 Boda (#E91E63)
- 💻 Tecnología (#607D8B)
- 🎯 Meta Personal (#FF9800)
- 🔮 Futuro (#673AB7)
- 💰 Inversión (#795548)

### 2. SavingsGoal

Metas principales de ahorro con lógica inteligente.

```javascript
{
  "id": "uuid",
  "user": "user_id",
  "category": "category_id",
  "name": "string(200)",
  "description": "text",
  "target_amount": "decimal(15,2)",
  "current_amount": "decimal(15,2)",    // Calculado automáticamente
  "currency": "string(3)",              // Por defecto COP
  "target_date": "date",
  "status": "string",                   // active, completed, paused, cancelled
  "priority": "string",                 // urgent, high, medium, low
  "auto_save_enabled": "boolean",
  "auto_save_amount": "decimal(15,2)",
  "auto_save_frequency": "string",      // daily, weekly, monthly
  "progress_percentage": "decimal(5,2)", // Calculado automáticamente
  "remaining_amount": "decimal(15,2)",   // Calculado automáticamente
  "daily_savings_needed": "decimal(15,2)", // Calculado automáticamente
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

### 3. SavingsDeposit

Registro de todas las transacciones (depósitos y retiros).

```javascript
{
  "id": "uuid",
  "savings_goal": "goal_id",
  "amount": "decimal(15,2)",
  "currency": "string(3)",
  "transaction_type": "string",         // deposit, withdrawal, adjustment, interest
  "description": "text",
  "date": "date",
  "is_automatic": "boolean",           // true para ahorros automáticos
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

### 4. SavingsTemplate

Plantillas expertas predefinidas para metas comunes.

```javascript
{
  "id": "uuid",
  "name": "string(200)",
  "description": "text",
  "category": "category_id",
  "suggested_amount": "decimal(15,2)",
  "suggested_timeframe_months": "integer",
  "priority": "string",
  "financial_advice": "text",
  "is_active": "boolean",
  "created_at": "datetime",
  "updated_at": "datetime"
}
```

---

## 🛠️ Endpoints de la API

### Base URL

```
/api/savings/
```

### Autenticación

Todos los endpoints requieren autenticación JWT:

```
Authorization: Bearer <token>
```

---

## 📁 Categorías de Ahorro

### GET /api/savings/categories/

Obtiene todas las categorías del usuario autenticado.

**Response:**

```javascript
[
  {
    id: "550e8400-e29b-41d4-a716-446655440000",
    name: "Emergencia",
    description:
      "Fondo para emergencias médicas, reparaciones urgentes y gastos inesperados",
    color: "#FF5722",
    icon: "emergency",
    is_default: true,
    created_at: "2024-01-15T10:30:00Z",
    updated_at: "2024-01-15T10:30:00Z",
  },
];
```

### POST /api/savings/categories/

Crea una nueva categoría personalizada.

**Request:**

```javascript
{
  "name": "Mi Meta Especial",
  "description": "Descripción de mi categoría personalizada",
  "color": "#2196F3",
  "icon": "star"
}
```

### PUT /api/savings/categories/{id}/

Actualiza una categoría existente.

### DELETE /api/savings/categories/{id}/

Elimina una categoría (solo si no tiene metas asociadas).

---

## 🎯 Metas de Ahorro

### GET /api/savings/goals/

Obtiene todas las metas del usuario.

**Parámetros de consulta:**

- `status`: active, completed, paused, cancelled
- `category`: ID de categoría
- `priority`: urgent, high, medium, low

**Response:**

```javascript
[
  {
    id: "660e8400-e29b-41d4-a716-446655440001",
    category: {
      id: "550e8400-e29b-41d4-a716-446655440000",
      name: "Emergencia",
      color: "#FF5722",
      icon: "emergency",
    },
    name: "Fondo de Emergencia",
    description: "6 meses de gastos de emergencia",
    target_amount: "15000000.00",
    current_amount: "5000000.00",
    currency: "COP",
    target_date: "2024-12-31",
    status: "active",
    priority: "urgent",
    auto_save_enabled: true,
    auto_save_amount: "500000.00",
    auto_save_frequency: "monthly",
    progress_percentage: "33.33",
    remaining_amount: "10000000.00",
    daily_savings_needed: "45662.10",
    created_at: "2024-01-15T10:30:00Z",
    updated_at: "2024-01-15T10:30:00Z",
  },
];
```

### POST /api/savings/goals/

Crea una nueva meta de ahorro.

**Request:**

```javascript
{
  "category": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Vacaciones en Europa",
  "description": "Viaje de 15 días por Europa",
  "target_amount": "8000000.00",
  "target_date": "2024-07-15",
  "priority": "high",
  "auto_save_enabled": true,
  "auto_save_amount": "300000.00",
  "auto_save_frequency": "weekly"
}
```

### GET /api/savings/goals/{id}/

Obtiene los detalles de una meta específica.

### PUT /api/savings/goals/{id}/

Actualiza una meta existente.

### DELETE /api/savings/goals/{id}/

Elimina una meta (solo si no tiene depósitos).

---

## 💰 Depósitos y Retiros

### GET /api/savings/deposits/

Obtiene todas las transacciones del usuario.

**Parámetros de consulta:**

- `savings_goal`: ID de la meta
- `transaction_type`: deposit, withdrawal, adjustment, interest
- `start_date`: YYYY-MM-DD
- `end_date`: YYYY-MM-DD

**Response:**

```javascript
[
  {
    id: "770e8400-e29b-41d4-a716-446655440002",
    savings_goal: {
      id: "660e8400-e29b-41d4-a716-446655440001",
      name: "Fondo de Emergencia",
    },
    amount: "500000.00",
    currency: "COP",
    transaction_type: "deposit",
    description: "Depósito mensual automático",
    date: "2024-01-15",
    is_automatic: true,
    created_at: "2024-01-15T10:30:00Z",
  },
];
```

### POST /api/savings/deposits/

Registra un nuevo depósito o retiro.

**Request:**

```javascript
{
  "savings_goal": "660e8400-e29b-41d4-a716-446655440001",
  "amount": "250000.00",
  "transaction_type": "deposit",
  "description": "Ahorro extra del bono",
  "date": "2024-01-20"
}
```

---

## 📊 Analytics y Estadísticas

### GET /api/savings/goals/analytics/

Obtiene estadísticas generales de todas las metas.

**Response:**

```javascript
{
  "total_goals": 5,
  "active_goals": 3,
  "completed_goals": 1,
  "total_saved": "12500000.00",
  "total_target": "35000000.00",
  "overall_progress": "35.71",
  "goals_by_priority": {
    "urgent": 1,
    "high": 2,
    "medium": 1,
    "low": 1
  },
  "goals_by_status": {
    "active": 3,
    "completed": 1,
    "paused": 1,
    "cancelled": 0
  },
  "monthly_deposits": "1500000.00",
  "goals_completion_prediction": {
    "this_month": 0,
    "next_3_months": 1,
    "next_6_months": 2,
    "next_12_months": 2
  }
}
```

### GET /api/savings/goals/category_distribution/

Distribución de metas por categoría.

**Response:**

```javascript
{
  "categories": ["Emergencia", "Vacaciones", "Casa"],
  "goal_counts": [1, 2, 1],
  "total_amounts": ["15000000.00", "12000000.00", "8000000.00"],
  "colors": ["#FF5722", "#00BCD4", "#4CAF50"]
}
```

### GET /api/savings/goals/progress_over_time/

Progreso histórico de ahorros.

**Parámetros:**

- `period`: week, month, quarter, year

**Response:**

```javascript
{
  "labels": ["Ene", "Feb", "Mar", "Abr"],
  "datasets": [
    {
      "label": "Total Ahorrado",
      "data": [2000000, 4500000, 7200000, 9800000],
      "backgroundColor": "#4CAF50"
    }
  ]
}
```

### GET /api/savings/goals/completion_forecast/

Pronóstico de completación de metas.

**Response:**

```javascript
{
  "forecast": [
    {
      "goal_id": "660e8400-e29b-41d4-a716-446655440001",
      "goal_name": "Fondo de Emergencia",
      "estimated_completion": "2024-08-15",
      "days_remaining": 142,
      "confidence": "high"
    }
  ]
}
```

---

## 📋 Plantillas Expertas

### GET /api/savings/templates/

Obtiene todas las plantillas disponibles.

**Response:**

```javascript
[
  {
    id: "880e8400-e29b-41d4-a716-446655440003",
    name: "Fondo de Emergencia Básico",
    description: "Fondo para cubrir 6 meses de gastos básicos",
    category: {
      id: "550e8400-e29b-41d4-a716-446655440000",
      name: "Emergencia",
      color: "#FF5722",
    },
    suggested_amount: "15000000.00",
    suggested_timeframe_months: 12,
    priority: "urgent",
    financial_advice: "Prioriza este fondo antes que cualquier otro ahorro...",
    is_active: true,
  },
];
```

### POST /api/savings/templates/{id}/create_goal/

Crea una meta basada en una plantilla.

**Request:**

```javascript
{
  "name": "Mi Fondo de Emergencia",
  "target_amount": "18000000.00",  // Opcional, usa suggested_amount por defecto
  "target_date": "2024-12-31",     // Opcional, calcula automáticamente
  "auto_save_enabled": true,
  "auto_save_amount": "600000.00",
  "auto_save_frequency": "monthly"
}
```

---

## 🔄 Endpoints de Acción

### POST /api/savings/goals/{id}/pause/

Pausa una meta activa.

### POST /api/savings/goals/{id}/resume/

Reanuda una meta pausada.

### POST /api/savings/goals/{id}/complete/

Marca una meta como completada.

### POST /api/savings/goals/{id}/cancel/

Cancela una meta.

### GET /api/savings/goals/{id}/recommendations/

Obtiene recomendaciones personalizadas para una meta.

**Response:**

```javascript
{
  "recommendations": [
    {
      "type": "increase_savings",
      "title": "Aumenta tu ahorro mensual",
      "description": "Con $50,000 adicionales mensuales completarías tu meta 2 meses antes",
      "priority": "medium",
      "potential_benefit": "Ahorro de tiempo: 2 meses"
    }
  ]
}
```

---

## 📈 Casos de Uso Comunes

### 1. Configuración Inicial del Usuario

```javascript
// 1. Las categorías se crean automáticamente al registrar al usuario
// 2. Obtener categorías disponibles
const categories = await fetch("/api/savings/categories/");

// 3. Obtener plantillas para mostrar sugerencias
const templates = await fetch("/api/savings/templates/");

// 4. Crear primera meta desde plantilla
const newGoal = await fetch("/api/savings/templates/template-id/create_goal/", {
  method: "POST",
  body: JSON.stringify({
    name: "Mi Fondo de Emergencia",
    auto_save_enabled: true,
    auto_save_amount: "500000.00",
    auto_save_frequency: "monthly",
  }),
});
```

### 2. Dashboard de Ahorros

```javascript
// Obtener estadísticas generales
const analytics = await fetch("/api/savings/goals/analytics/");

// Obtener metas activas
const activeGoals = await fetch("/api/savings/goals/?status=active");

// Obtener distribución por categorías
const distribution = await fetch("/api/savings/goals/category_distribution/");

// Obtener progreso histórico
const progress = await fetch(
  "/api/savings/goals/progress_over_time/?period=month"
);
```

### 3. Gestión de Meta Individual

```javascript
// Obtener detalles de la meta
const goal = await fetch(`/api/savings/goals/${goalId}/`);

// Registrar depósito
const deposit = await fetch("/api/savings/deposits/", {
  method: "POST",
  body: JSON.stringify({
    savings_goal: goalId,
    amount: "100000.00",
    transaction_type: "deposit",
    description: "Ahorro semanal",
  }),
});

// Obtener recomendaciones
const recommendations = await fetch(
  `/api/savings/goals/${goalId}/recommendations/`
);
```

---

## ⚙️ Configuración y Estados

### Estados de Meta

- **active**: Meta en progreso
- **completed**: Meta alcanzada
- **paused**: Meta temporalmente suspendida
- **cancelled**: Meta cancelada

### Prioridades

- **urgent**: Emergencias, deudas críticas
- **high**: Metas importantes a corto plazo
- **medium**: Metas a mediano plazo
- **low**: Metas a largo plazo o secundarias

### Frecuencias de Ahorro Automático

- **daily**: Diario
- **weekly**: Semanal
- **monthly**: Mensual

### Tipos de Transacción

- **deposit**: Depósito manual
- **withdrawal**: Retiro
- **adjustment**: Ajuste manual
- **interest**: Intereses ganados

---

## 🔒 Seguridad y Validaciones

### Validaciones del Frontend

```javascript
// Validación de monto objetivo
if (targetAmount <= 0) {
  throw new Error("El monto objetivo debe ser mayor a 0");
}

// Validación de fecha objetivo
const today = new Date();
if (targetDate <= today) {
  throw new Error("La fecha objetivo debe ser futura");
}

// Validación de ahorro automático
if (autoSaveEnabled && autoSaveAmount <= 0) {
  throw new Error("El monto de ahorro automático debe ser mayor a 0");
}
```

### Permisos

- Usuarios solo pueden ver/editar sus propias metas y categorías
- Las categorías predefinidas no se pueden eliminar
- Las metas con depósitos no se pueden eliminar directamente

---

## 📱 Ejemplos de Integración Frontend

### React/Vue Component Example

```javascript
// Componente de Meta de Ahorro
const SavingsGoalCard = ({ goal }) => {
  const progressPercent = goal.progress_percentage;
  const isCompleted = goal.status === "completed";

  return (
    <div className="savings-goal-card">
      <div className="goal-header">
        <span className="category-icon" style={{ color: goal.category.color }}>
          {goal.category.icon}
        </span>
        <h3>{goal.name}</h3>
        <span className={`priority ${goal.priority}`}>{goal.priority}</span>
      </div>

      <div className="progress-bar">
        <div
          className="progress-fill"
          style={{
            width: `${progressPercent}%`,
            backgroundColor: goal.category.color,
          }}
        />
      </div>

      <div className="goal-amounts">
        <span>
          {formatCurrency(goal.current_amount)} /
          {formatCurrency(goal.target_amount)}
        </span>
        <span className="progress-text">{progressPercent}% completado</span>
      </div>

      {!isCompleted && (
        <div className="daily-recommendation">
          Ahorra {formatCurrency(goal.daily_savings_needed)} diarios para
          completar a tiempo
        </div>
      )}
    </div>
  );
};
```

---

## 🚀 Mejores Prácticas

### 1. Manejo de Estados de Carga

```javascript
const [goals, setGoals] = useState([]);
const [loading, setLoading] = useState(true);
const [error, setError] = useState(null);

useEffect(() => {
  const fetchGoals = async () => {
    try {
      setLoading(true);
      const response = await api.get("/savings/goals/");
      setGoals(response.data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  fetchGoals();
}, []);
```

### 2. Optimización de Consultas

```javascript
// Usar parámetros de consulta para filtrar
const fetchGoalsByCategory = async (categoryId) => {
  return await api.get(`/savings/goals/?category=${categoryId}`);
};

// Paginación para listas largas
const fetchDeposits = async (page = 1, pageSize = 20) => {
  return await api.get(`/savings/deposits/?page=${page}&page_size=${pageSize}`);
};
```

### 3. Actualización en Tiempo Real

```javascript
// Actualizar meta después de depósito
const handleDeposit = async (goalId, amount) => {
  await api.post("/savings/deposits/", {
    savings_goal: goalId,
    amount: amount,
    transaction_type: "deposit",
  });

  // Refrescar la meta actualizada
  const updatedGoal = await api.get(`/savings/goals/${goalId}/`);
  updateGoalInState(updatedGoal.data);
};
```

---

## 📞 Soporte y Contacto

Para dudas sobre la implementación:

- **Backend Developer**: Disponible para consultas técnicas
- **Documentación Actualizada**: Este documento se actualiza con cada cambio en la API
- **Ambiente de Pruebas**: Disponible en `/api/savings/` con datos de ejemplo

¡El módulo está listo para implementación frontend! 🎉
