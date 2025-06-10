# 🚀 Guía de Configuración - Módulo de Ahorros

## 📋 Configuración Inicial

### 1. Comandos de Backend Disponibles

```bash
# Crear categorías predefinidas para un usuario específico
python manage.py create_user_savings_categories --user testuser

# Crear categorías para todos los usuarios
python manage.py create_user_savings_categories --all-users

# Crear plantillas basadas en categorías de un usuario
python manage.py create_user_savings_templates --user testuser

# Crear plantillas para todos los usuarios
python manage.py create_user_savings_templates --all-users
```

### 2. Proceso Automático para Nuevos Usuarios

Al registrar un nuevo usuario, automáticamente se crean:

- ✅ 10 categorías predefinidas con colores e íconos
- ✅ 10 plantillas expertas con recomendaciones financieras

---

## 🧪 Datos de Ejemplo

### Categorías Predefinidas Completas

```javascript
[
  {
    id: "uuid-1",
    name: "Emergencia",
    description:
      "Fondo para emergencias médicas, reparaciones urgentes y gastos inesperados",
    color: "#FF5722",
    icon: "emergency",
    is_default: true,
  },
  {
    id: "uuid-2",
    name: "Vacaciones",
    description: "Ahorros para viajes, vacaciones y experiencias de descanso",
    color: "#00BCD4",
    icon: "beach_access",
    is_default: true,
  },
  {
    id: "uuid-3",
    name: "Casa",
    description: "Ahorros para compra de vivienda, inicial, mejoras del hogar",
    color: "#4CAF50",
    icon: "home",
    is_default: true,
  },
  {
    id: "uuid-4",
    name: "Vehículo",
    description: "Compra de carro, moto, mantenimiento y reparaciones",
    color: "#9C27B0",
    icon: "directions_car",
    is_default: true,
  },
  {
    id: "uuid-5",
    name: "Educación",
    description:
      "Cursos, carreras, especializaciones, libros y material educativo",
    color: "#3F51B5",
    icon: "school",
    is_default: true,
  },
  {
    id: "uuid-6",
    name: "Boda",
    description: "Planificación y gastos de matrimonio, luna de miel",
    color: "#E91E63",
    icon: "favorite",
    is_default: true,
  },
  {
    id: "uuid-7",
    name: "Tecnología",
    description: "Computadores, celulares, gadgets y equipos tecnológicos",
    color: "#607D8B",
    icon: "computer",
    is_default: true,
  },
  {
    id: "uuid-8",
    name: "Meta Personal",
    description: "Proyectos personales, hobbies, emprendimientos",
    color: "#FF9800",
    icon: "star",
    is_default: true,
  },
  {
    id: "uuid-9",
    name: "Futuro",
    description: "Ahorros a largo plazo para jubilación y metas futuras",
    color: "#673AB7",
    icon: "trending_up",
    is_default: true,
  },
  {
    id: "uuid-10",
    name: "Inversión",
    description: "Capital para inversiones, acciones, bonos y portafolio",
    color: "#795548",
    icon: "account_balance",
    is_default: true,
  },
];
```

### Plantillas Expertas Completas

```javascript
[
  {
    id: "template-1",
    name: "Fondo de Emergencia Básico",
    description:
      "Fondo para cubrir 6 meses de gastos básicos en caso de emergencias médicas, pérdida de empleo o gastos inesperados urgentes.",
    category: "Emergencia",
    suggested_amount: "15000000.00",
    suggested_timeframe_months: 12,
    priority: "urgent",
    financial_advice:
      "PRIORIDAD MÁXIMA: Este fondo debe ser tu primera meta de ahorro. Representa 6 meses de gastos básicos y te dará tranquilidad financiera. Recomendamos ahorrar primero $5M (gastos de 2 meses) como meta inicial.",
  },
  {
    id: "template-2",
    name: "Vacaciones Familiares",
    description:
      "Planifica unas vacaciones familiares memorables sin comprometer tu estabilidad financiera.",
    category: "Vacaciones",
    suggested_amount: "8000000.00",
    suggested_timeframe_months: 18,
    priority: "medium",
    financial_advice:
      "Las vacaciones son importantes para el bienestar familiar. Planifica con 12-18 meses de anticipación para obtener mejores precios y distribuir el costo. Considera destinos nacionales para optimizar tu presupuesto.",
  },
  {
    id: "template-3",
    name: "Inicial para Vivienda",
    description: "Ahorra para el enganche de tu primera vivienda propia.",
    category: "Casa",
    suggested_amount: "50000000.00",
    suggested_timeframe_months: 60,
    priority: "high",
    financial_advice:
      "Para vivienda VIS puedes dar inicial desde 10%. Para vivienda No VIS mínimo 20%. Complementa con ahorro programado y cesantías. Considera subsidios gubernamentales disponibles.",
  },
  {
    id: "template-4",
    name: "Vehículo Familiar",
    description:
      "Compra tu vehículo ideal para transporte familiar seguro y cómodo.",
    category: "Vehículo",
    suggested_amount: "25000000.00",
    suggested_timeframe_months: 36,
    priority: "medium",
    financial_advice:
      "Un vehículo es una inversión importante. Considera vehículos usados en buen estado para optimizar tu dinero. El costo total incluye: inicial, cuotas, seguro, mantenimiento y combustible.",
  },
  {
    id: "template-5",
    name: "Especialización Profesional",
    description:
      "Invierte en tu crecimiento profesional con una especialización o maestría.",
    category: "Educación",
    suggested_amount: "20000000.00",
    suggested_timeframe_months: 24,
    priority: "high",
    financial_advice:
      "La educación es la mejor inversión a largo plazo. Una especialización puede aumentar tus ingresos 30-50%. Investiga opciones de financiación educativa y becas disponibles.",
  },
  {
    id: "template-6",
    name: "Boda de Ensueño",
    description: "Celebra tu matrimonio con la boda perfecta sin endeudarte.",
    category: "Boda",
    suggested_amount: "30000000.00",
    suggested_timeframe_months: 24,
    priority: "medium",
    financial_advice:
      "Una boda hermosa no tiene que arruinar tus finanzas. Planifica con 2 años de anticipación, prioriza lo realmente importante y considera celebraciones íntimas que pueden ser igual de especiales.",
  },
  {
    id: "template-7",
    name: "Upgrade Tecnológico",
    description:
      "Mantente actualizado con la tecnología que necesitas para trabajo y entretenimiento.",
    category: "Tecnología",
    suggested_amount: "6000000.00",
    suggested_timeframe_months: 12,
    priority: "low",
    financial_advice:
      "La tecnología evoluciona rápido. Compra equipos que realmente necesites y que tengan buena relación costo-beneficio. Considera equipos reacondicionados de marcas reconocidas.",
  },
  {
    id: "template-8",
    name: "Proyecto Personal",
    description:
      "Financia tu emprendimiento, hobby o proyecto que te apasiona.",
    category: "Meta Personal",
    suggested_amount: "12000000.00",
    suggested_timeframe_months: 18,
    priority: "medium",
    financial_advice:
      "Los proyectos personales enriquecen tu vida y pueden generar ingresos adicionales. Planifica bien los costos, empieza pequeño y reinvierte las ganancias para hacer crecer tu proyecto.",
  },
  {
    id: "template-9",
    name: "Fondo de Jubilación",
    description:
      "Asegura tu futuro financiero con un fondo de jubilación complementario.",
    category: "Futuro",
    suggested_amount: "100000000.00",
    suggested_timeframe_months: 240,
    priority: "high",
    financial_advice:
      "Empieza a ahorrar para tu jubilación cuanto antes. El interés compuesto es tu mejor aliado. Incluso $200,000 mensuales durante 20 años pueden generar un fondo significativo para tu retiro.",
  },
  {
    id: "template-10",
    name: "Capital de Inversión",
    description:
      "Crea tu capital inicial para comenzar a invertir en el mercado financiero.",
    category: "Inversión",
    suggested_amount: "10000000.00",
    suggested_timeframe_months: 24,
    priority: "medium",
    financial_advice:
      "Antes de invertir, ten tu fondo de emergencia completo. Empieza con inversiones conservadoras como CDTs o fondos de inversión. Nunca inviertas dinero que no puedes permitirte perder.",
  },
];
```

---

## 🎯 Ejemplos de Flujos de Trabajo

### Flujo 1: Usuario Nuevo - Primera Meta

```javascript
// 1. Usuario se registra
// 2. Sistema crea automáticamente categorías y plantillas

// 3. Frontend obtiene plantillas para mostrar sugerencias
const response = await fetch("/api/savings/templates/");
const templates = await response.json();

// 4. Usuario selecciona "Fondo de Emergencia Básico"
const emergencyTemplate = templates.find((t) => t.name.includes("Emergencia"));

// 5. Crear meta desde plantilla
const newGoalResponse = await fetch(
  `/api/savings/templates/${emergencyTemplate.id}/create_goal/`,
  {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + userToken,
    },
    body: JSON.stringify({
      name: "Mi Fondo de Emergencia",
      target_amount: "10000000.00", // Usuario ajusta el monto
      auto_save_enabled: true,
      auto_save_amount: "400000.00",
      auto_save_frequency: "monthly",
    }),
  }
);

const newGoal = await newGoalResponse.json();
console.log("Meta creada:", newGoal);
```

### Flujo 2: Dashboard Principal

```javascript
// Obtener datos para el dashboard principal
const dashboardData = await Promise.all([
  fetch("/api/savings/goals/analytics/").then((r) => r.json()),
  fetch("/api/savings/goals/?status=active").then((r) => r.json()),
  fetch("/api/savings/goals/category_distribution/").then((r) => r.json()),
  fetch("/api/savings/goals/progress_over_time/?period=month").then((r) =>
    r.json()
  ),
]);

const [analytics, activeGoals, distribution, progressHistory] = dashboardData;

// Datos listos para mostrar en dashboard
console.log("Analytics:", analytics);
console.log("Metas activas:", activeGoals.length);
console.log("Distribución:", distribution);
console.log("Progreso histórico:", progressHistory);
```

### Flujo 3: Registrar Depósito y Actualizar UI

```javascript
const handleDeposit = async (goalId, amount, description) => {
  try {
    // 1. Registrar depósito
    const depositResponse = await fetch("/api/savings/deposits/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + userToken,
      },
      body: JSON.stringify({
        savings_goal: goalId,
        amount: amount,
        transaction_type: "deposit",
        description: description,
        date: new Date().toISOString().split("T")[0],
      }),
    });

    if (!depositResponse.ok) throw new Error("Error al registrar depósito");

    // 2. Obtener meta actualizada
    const updatedGoalResponse = await fetch(`/api/savings/goals/${goalId}/`);
    const updatedGoal = await updatedGoalResponse.json();

    // 3. Actualizar estado en la UI
    updateGoalInUI(updatedGoal);
    showSuccessMessage(
      `Depósito de ${formatCurrency(amount)} registrado exitosamente`
    );

    // 4. Opcional: Actualizar analytics si es necesario
    refreshAnalytics();
  } catch (error) {
    showErrorMessage("Error al registrar el depósito: " + error.message);
  }
};
```

---

## 📊 Componentes UI Recomendados

### 1. Tarjeta de Meta de Ahorro

```javascript
const SavingsGoalCard = ({ goal, onDeposit, onEdit }) => {
  const progressPercent = parseFloat(goal.progress_percentage);
  const isCompleted = goal.status === "completed";
  const isUrgent = goal.priority === "urgent";

  return (
    <div
      className={`goal-card ${isUrgent ? "urgent" : ""} ${
        isCompleted ? "completed" : ""
      }`}
    >
      {/* Header con categoría */}
      <div className="goal-header">
        <div className="category-info">
          <span
            className="category-icon"
            style={{ backgroundColor: goal.category.color }}
          >
            {getIcon(goal.category.icon)}
          </span>
          <div>
            <h3>{goal.name}</h3>
            <span className="category-name">{goal.category.name}</span>
          </div>
        </div>
        <span className={`priority-badge ${goal.priority}`}>
          {getPriorityLabel(goal.priority)}
        </span>
      </div>

      {/* Barra de progreso */}
      <div className="progress-section">
        <div className="progress-bar">
          <div
            className="progress-fill"
            style={{
              width: `${Math.min(progressPercent, 100)}%`,
              backgroundColor: goal.category.color,
            }}
          />
        </div>
        <div className="progress-text">
          {progressPercent.toFixed(1)}% completado
        </div>
      </div>

      {/* Montos */}
      <div className="amounts-section">
        <div className="current-amount">
          <span className="label">Ahorrado:</span>
          <span className="amount">{formatCurrency(goal.current_amount)}</span>
        </div>
        <div className="target-amount">
          <span className="label">Meta:</span>
          <span className="amount">{formatCurrency(goal.target_amount)}</span>
        </div>
        <div className="remaining-amount">
          <span className="label">Falta:</span>
          <span className="amount">
            {formatCurrency(goal.remaining_amount)}
          </span>
        </div>
      </div>

      {/* Recomendación diaria */}
      {!isCompleted && (
        <div className="daily-recommendation">
          💡 Ahorra {formatCurrency(goal.daily_savings_needed)} diarios para
          completar el {formatDate(goal.target_date)}
        </div>
      )}

      {/* Acciones */}
      <div className="actions">
        <button
          className="btn-primary"
          onClick={() => onDeposit(goal.id)}
          disabled={isCompleted}
        >
          Agregar Dinero
        </button>
        <button className="btn-secondary" onClick={() => onEdit(goal.id)}>
          Editar
        </button>
      </div>
    </div>
  );
};
```

### 2. Modal de Nuevo Depósito

```javascript
const DepositModal = ({ goal, isOpen, onClose, onSuccess }) => {
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    try {
      const response = await fetch("/api/savings/deposits/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + getAuthToken(),
        },
        body: JSON.stringify({
          savings_goal: goal.id,
          amount: parseFloat(amount),
          transaction_type: "deposit",
          description: description || `Depósito para ${goal.name}`,
          date: new Date().toISOString().split("T")[0],
        }),
      });

      if (!response.ok) throw new Error("Error al registrar depósito");

      onSuccess();
      onClose();
      showSuccess(
        `¡Depósito de ${formatCurrency(amount)} registrado exitosamente!`
      );
    } catch (error) {
      showError("Error al registrar el depósito: " + error.message);
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <div className="modal-header">
          <h2>Agregar Dinero</h2>
          <button className="close-btn" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="goal-summary">
          <span className="goal-name">{goal.name}</span>
          <span className="progress">
            {goal.progress_percentage}% completado
          </span>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label htmlFor="amount">Monto a depositar</label>
            <input
              type="number"
              id="amount"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="Ejemplo: 100000"
              min="1000"
              step="1000"
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="description">Descripción (opcional)</label>
            <input
              type="text"
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Ejemplo: Ahorro quincenal"
            />
          </div>

          <div className="prediction">
            {amount && (
              <p>
                Con este depósito tendrás{" "}
                {formatCurrency(
                  parseFloat(goal.current_amount) + parseFloat(amount || 0)
                )}{" "}
                de {formatCurrency(goal.target_amount)}
              </p>
            )}
          </div>

          <div className="modal-actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={onClose}
              disabled={loading}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={loading || !amount}
            >
              {loading ? "Guardando..." : "Registrar Depósito"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
```

### 3. Selector de Plantillas

```javascript
const TemplateSelector = ({ templates, onSelectTemplate }) => {
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [selectedPriority, setSelectedPriority] = useState("all");

  const filteredTemplates = templates.filter((template) => {
    return (
      (selectedCategory === "all" ||
        template.category.name === selectedCategory) &&
      (selectedPriority === "all" || template.priority === selectedPriority)
    );
  });

  return (
    <div className="template-selector">
      <div className="filters">
        <select
          value={selectedCategory}
          onChange={(e) => setSelectedCategory(e.target.value)}
        >
          <option value="all">Todas las categorías</option>
          <option value="Emergencia">Emergencia</option>
          <option value="Vacaciones">Vacaciones</option>
          <option value="Casa">Casa</option>
          {/* Más opciones... */}
        </select>

        <select
          value={selectedPriority}
          onChange={(e) => setSelectedPriority(e.target.value)}
        >
          <option value="all">Todas las prioridades</option>
          <option value="urgent">Urgente</option>
          <option value="high">Alta</option>
          <option value="medium">Media</option>
          <option value="low">Baja</option>
        </select>
      </div>

      <div className="templates-grid">
        {filteredTemplates.map((template) => (
          <div
            key={template.id}
            className={`template-card priority-${template.priority}`}
            onClick={() => onSelectTemplate(template)}
          >
            <div className="template-header">
              <span
                className="category-color"
                style={{ backgroundColor: template.category.color }}
              />
              <h3>{template.name}</h3>
              <span className={`priority-badge ${template.priority}`}>
                {getPriorityLabel(template.priority)}
              </span>
            </div>

            <p className="template-description">{template.description}</p>

            <div className="template-details">
              <div className="suggested-amount">
                <strong>{formatCurrency(template.suggested_amount)}</strong>
                <span>en {template.suggested_timeframe_months} meses</span>
              </div>
            </div>

            <div className="financial-advice">
              <strong>💡 Consejo:</strong>
              <p>{template.financial_advice.substring(0, 100)}...</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
```

---

## 🎨 Estilos CSS Recomendados

```css
/* Tarjeta de meta de ahorro */
.goal-card {
  background: white;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  border: 1px solid #e0e0e0;
  transition: all 0.3s ease;
}

.goal-card.urgent {
  border-left: 4px solid #ff5722;
}

.goal-card.completed {
  background: #f8f9fa;
  border-left: 4px solid #4caf50;
}

/* Barra de progreso */
.progress-bar {
  width: 100%;
  height: 8px;
  background-color: #e0e0e0;
  border-radius: 4px;
  overflow: hidden;
  margin: 12px 0;
}

.progress-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease;
}

/* Badges de prioridad */
.priority-badge {
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
}

.priority-badge.urgent {
  background: #ffebee;
  color: #c62828;
}
.priority-badge.high {
  background: #fff3e0;
  color: #ef6c00;
}
.priority-badge.medium {
  background: #f3e5f5;
  color: #7b1fa2;
}
.priority-badge.low {
  background: #e8f5e8;
  color: #2e7d32;
}

/* Iconos de categoría */
.category-icon {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 20px;
}

/* Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: white;
  border-radius: 12px;
  padding: 24px;
  max-width: 500px;
  width: 90%;
  max-height: 90vh;
  overflow-y: auto;
}
```

---

## 🔧 Utilidades JavaScript

```javascript
// Formatear moneda colombiana
const formatCurrency = (amount) => {
  return new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency: "COP",
    minimumFractionDigits: 0,
  }).format(amount);
};

// Formatear fecha
const formatDate = (dateString) => {
  return new Date(dateString).toLocaleDateString("es-CO", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
};

// Obtener etiqueta de prioridad
const getPriorityLabel = (priority) => {
  const labels = {
    urgent: "Urgente",
    high: "Alta",
    medium: "Media",
    low: "Baja",
  };
  return labels[priority] || priority;
};

// Calcular días restantes
const getDaysRemaining = (targetDate) => {
  const today = new Date();
  const target = new Date(targetDate);
  const diffTime = target - today;
  const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
  return diffDays;
};

// Validar monto
const validateAmount = (amount) => {
  const numAmount = parseFloat(amount);
  if (isNaN(numAmount) || numAmount <= 0) {
    throw new Error("El monto debe ser un número mayor a 0");
  }
  if (numAmount < 1000) {
    throw new Error("El monto mínimo es $1,000");
  }
  return true;
};
```

---

## 📱 Testing Frontend

### Datos de Prueba

```javascript
// Mock data para testing
const mockGoals = [
  {
    id: "test-goal-1",
    category: {
      id: "cat-1",
      name: "Emergencia",
      color: "#FF5722",
      icon: "emergency",
    },
    name: "Fondo de Emergencia",
    target_amount: "15000000.00",
    current_amount: "5000000.00",
    progress_percentage: "33.33",
    status: "active",
    priority: "urgent",
    daily_savings_needed: "45662.10",
  },
];

// Tests básicos
describe("SavingsGoalCard", () => {
  test("shows correct progress percentage", () => {
    render(<SavingsGoalCard goal={mockGoals[0]} />);
    expect(screen.getByText("33.3% completado")).toBeInTheDocument();
  });

  test("shows urgent priority correctly", () => {
    render(<SavingsGoalCard goal={mockGoals[0]} />);
    expect(screen.getByText("Urgente")).toBeInTheDocument();
  });
});
```

¡La documentación está completa y lista para el equipo de frontend! 🚀
