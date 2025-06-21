# 📋 Guía de Migración del Frontend - Categorías por Usuario

## 🎯 **Resumen de Cambios**

Este documento describe todos los cambios necesarios en el frontend para adaptarse al nuevo sistema de **categorías por usuario**. La migración está diseñada para mantener **compatibilidad total** con el código existente mientras proporciona acceso a las nuevas funcionalidades.

### **✅ Lo que NO cambia (Compatibilidad Garantizada):**

- **URLs existentes** siguen funcionando
- **Estructura de respuestas** de endpoints existentes se mantiene
- **Campos legacy** aún están disponibles en las respuestas
- **Frontend actual** puede seguir funcionando sin cambios

### **🚀 Lo que se Añade (Nuevas Capacidades):**

- **36 nuevos endpoints** para gestión completa de categorías por usuario
- **Campos adicionales** en respuestas existentes para categorías por usuario
- **APIs dedicadas** para crear, editar y gestionar categorías personalizadas
- **Sistema híbrido** que prioriza categorías del usuario sobre globales

---

## 📊 **Nuevos Endpoints Disponibles**

### **🏷️ Categorías de Gastos por Usuario**

#### **Base URL:** `/api/categories/expenses/`

| Endpoint                                      | Método      | Descripción                                       | Body/Parámetros Requeridos                             |
| --------------------------------------------- | ----------- | ------------------------------------------------- | ------------------------------------------------------ |
| `/api/categories/expenses/`                   | `GET`       | Listar todas las categorías de gastos del usuario | -                                                      |
| `/api/categories/expenses/`                   | `POST`      | Crear nueva categoría de gastos                   | `name` (requerido), `description`, `examples`, `color` |
| `/api/categories/expenses/{id}/`              | `GET`       | Obtener categoría específica                      | -                                                      |
| `/api/categories/expenses/{id}/`              | `PUT/PATCH` | Actualizar categoría                              | `name`, `description`, `examples`, `color`             |
| `/api/categories/expenses/{id}/`              | `DELETE`    | Eliminar categoría                                | -                                                      |
| `/api/categories/expenses/default/`           | `GET`       | Listar solo categorías predefinidas               | -                                                      |
| `/api/categories/expenses/custom/`            | `GET`       | Listar solo categorías personalizadas             | -                                                      |
| `/api/categories/expenses/with_usage/`        | `GET`       | Categorías con estadísticas de uso                | Query: `days` (opcional, default: 30)                  |
| `/api/categories/expenses/colors_map/`        | `GET`       | Mapa de colores para visualizaciones              | -                                                      |
| `/api/categories/expenses/search/`            | `GET`       | Buscar categorías por nombre                      | Query: `q` (requerido)                                 |
| `/api/categories/expenses/popular/`           | `GET`       | Categorías más usadas                             | Query: `limit` (opcional, default: 10)                 |
| `/api/categories/expenses/recent/`            | `GET`       | Categorías usadas recientemente                   | Query: `limit` (opcional, default: 10)                 |
| `/api/categories/expenses/bulk_create/`       | `POST`      | Crear múltiples categorías                        | `categories[]` array de objetos                        |
| `/api/categories/expenses/bulk_update/`       | `PATCH`     | Actualizar múltiples categorías                   | `categories[]` array con `id`                          |
| `/api/categories/expenses/bulk_delete/`       | `DELETE`    | Eliminar múltiples categorías                     | `ids[]` array de IDs                                   |
| `/api/categories/expenses/export/`            | `GET`       | Exportar categorías del usuario                   | Query: `format` (json/csv)                             |
| `/api/categories/expenses/import/`            | `POST`      | Importar categorías                               | `file` o `data` (JSON)                                 |
| `/api/categories/expenses/reset_to_defaults/` | `POST`      | Restaurar categorías predefinidas                 | -                                                      |

#### **📝 Ejemplos de Requests para Categorías de Gastos:**

**Crear categoría de gasto:**

```javascript
POST /api/categories/expenses/
Content-Type: application/json

{
  "name": "Mascota",
  "description": "Gastos relacionados con mi mascota",
  "examples": "Comida para perro, veterinario, juguetes",
  "color": "#FF9800"
}
```

**Actualizar categoría:**

```javascript
PATCH /api/categories/expenses/123/
Content-Type: application/json

{
  "description": "Gastos de alimentación y cuidado de mascota",
  "color": "#FFC107"
}
```

**Búsqueda de categorías:**

```javascript
GET /api/categories/expenses/search/?q=comida
```

**Crear múltiples categorías:**

```javascript
POST /api/categories/expenses/bulk_create/
Content-Type: application/json

{
  "categories": [
    {
      "name": "Educación",
      "description": "Gastos educativos",
      "examples": "Cursos, libros, certificaciones",
      "color": "#2196F3"
    },
    {
      "name": "Tecnología",
      "description": "Compras de tecnología",
      "examples": "Laptop, celular, software",
      "color": "#9C27B0"
    }
  ]
}
```

### **💰 Categorías de Ingresos por Usuario**

#### **Base URL:** `/api/categories/incomes/`

| Endpoint                                     | Método      | Descripción                                         | Body/Parámetros Requeridos                            |
| -------------------------------------------- | ----------- | --------------------------------------------------- | ----------------------------------------------------- |
| `/api/categories/incomes/`                   | `GET`       | Listar todas las categorías de ingresos del usuario | -                                                     |
| `/api/categories/incomes/`                   | `POST`      | Crear nueva categoría de ingresos                   | `name` (requerido), `description`, `example`, `color` |
| `/api/categories/incomes/{id}/`              | `GET`       | Obtener categoría específica                        | -                                                     |
| `/api/categories/incomes/{id}/`              | `PUT/PATCH` | Actualizar categoría                                | `name`, `description`, `example`, `color`             |
| `/api/categories/incomes/{id}/`              | `DELETE`    | Eliminar categoría                                  | -                                                     |
| `/api/categories/incomes/default/`           | `GET`       | Listar solo categorías predefinidas                 | -                                                     |
| `/api/categories/incomes/custom/`            | `GET`       | Listar solo categorías personalizadas               | -                                                     |
| `/api/categories/incomes/with_usage/`        | `GET`       | Categorías con estadísticas de uso                  | Query: `days` (opcional, default: 30)                 |
| `/api/categories/incomes/colors_map/`        | `GET`       | Mapa de colores para visualizaciones                | -                                                     |
| `/api/categories/incomes/search/`            | `GET`       | Buscar categorías por nombre                        | Query: `q` (requerido)                                |
| `/api/categories/incomes/popular/`           | `GET`       | Categorías más usadas                               | Query: `limit` (opcional, default: 10)                |
| `/api/categories/incomes/recent/`            | `GET`       | Categorías usadas recientemente                     | Query: `limit` (opcional, default: 10)                |
| `/api/categories/incomes/bulk_create/`       | `POST`      | Crear múltiples categorías                          | `categories[]` array de objetos                       |
| `/api/categories/incomes/bulk_update/`       | `PATCH`     | Actualizar múltiples categorías                     | `categories[]` array con `id`                         |
| `/api/categories/incomes/bulk_delete/`       | `DELETE`    | Eliminar múltiples categorías                       | `ids[]` array de IDs                                  |
| `/api/categories/incomes/export/`            | `GET`       | Exportar categorías del usuario                     | Query: `format` (json/csv)                            |
| `/api/categories/incomes/import/`            | `POST`      | Importar categorías                                 | `file` o `data` (JSON)                                |
| `/api/categories/incomes/reset_to_defaults/` | `POST`      | Restaurar categorías predefinidas                   | -                                                     |

#### **📝 Ejemplos de Requests para Categorías de Ingresos:**

**Crear categoría de ingreso:**

```javascript
POST /api/categories/incomes/
Content-Type: application/json

{
  "name": "Inversiones",
  "description": "Ingresos por inversiones financieras",
  "example": "Dividendos, intereses, ganancias de capital",
  "color": "#4CAF50"
}
```

**Eliminar múltiples categorías:**

```javascript
DELETE /api/categories/incomes/bulk_delete/
Content-Type: application/json

{
  "ids": [123, 124, 125]
}
```

**Exportar categorías:**

```javascript
GET /api/categories/incomes/export/?format=json
```

### **🔗 Endpoints Combinados**

#### **Base URL:** `/api/categories/`

| Endpoint                        | Método | Descripción                                          | Body/Parámetros                  |
| ------------------------------- | ------ | ---------------------------------------------------- | -------------------------------- |
| `/api/categories/all/`          | `GET`  | Todas las categorías del usuario (gastos + ingresos) | -                                |
| `/api/categories/with-details/` | `GET`  | Categorías con información completa                  | -                                |
| `/api/categories/summary/`      | `GET`  | Resumen estadístico de categorías                    | -                                |
| `/api/categories/search/`       | `POST` | Búsqueda avanzada en todas las categorías            | `query`, `type` (expense/income) |

**Ejemplo de búsqueda combinada:**

```javascript
POST /api/categories/search/
Content-Type: application/json

{
  "query": "comida",
  "type": "expense",  // opcional: "expense", "income", o ambos
  "include_usage": true  // incluir estadísticas de uso
}
```

---

## 🔄 **Cambios en Endpoints Existentes**

### **📈 Endpoints de Gastos - Cambios Implementados:**

#### **`/api/expenses/` (CRUD de gastos)**

- **✅ Compatible:** Funciona igual que antes
- **🆕 Nuevo campo:** `user_expense_category` en respuestas
- **📝 Comportamiento:** Prioriza categorías del usuario en visualizaciones

**Crear gasto (actualizado):**

```javascript
POST /api/expenses/
Content-Type: application/json

{
  "amount": 50000,
  "currency": "COP",
  "description": "Gasolina para el carro",
  "spent_at": "2025-01-15",

  // ✅ Recomendado: Usar ID de categoría del usuario
  "user_expense_category": 123,

  // 📱 Legacy: Aún funciona para compatibilidad
  "category": 15
}
```

#### **`/api/expenses/by_category/`**

- **✅ Compatible:** Misma estructura de respuesta
- **🔄 Lógica:** Ahora agrupa por categorías del usuario automáticamente
- **🆕 Información:** Incluye metadatos de categorías personalizadas

**Respuesta mejorada:**

```javascript
GET /
  api /
  expenses /
  by_category /
  // Respuesta:
  {
    categories: ["Transporte y Movilidad", "Alimentación"],
    totals: [150000, 300000],
    colors: ["#FF5722", "#4CAF50"],
    descriptions: ["Gastos de movilidad urbana", "Comida y restaurantes"],
    examples: ["Uber, taxi, gasolina", "Almuerzo, cena, mercado"],
    categories_info: [
      {
        name: "Transporte y Movilidad",
        color: "#FF5722",
        is_default: true,
        usage_count: 15,
        total_amount: 150000,
      },
    ],
  };
```

#### **`/api/expenses/summary/`**

- **✅ Compatible:** Formato de respuesta igual
- **🔄 Datos:** Usa categorías por usuario para cálculos
- **📊 Precisión:** Resultados más personalizados por usuario

#### **`/api/expenses/weekly_by_category/`**

- **✅ Compatible:** APIs existentes funcionan
- **🔄 Agrupación:** Por categorías del usuario
- **🎨 Colores:** Usa colores personalizados del usuario

#### **Todos los endpoints de gráficos:**

- `/api/expenses/donut_chart_data/`
- `/api/expenses/bar_chart_data/`
- `/api/expenses/line_chart_data/`
- `/api/expenses/stacked_bar_chart_data/`
- `/api/expenses/monthly_comparison_chart_data/`

**🔄 Cambios aplicados:**

- **Categorización:** Usa categorías por usuario
- **Colores:** Respeta colores personalizados
- **Datos:** Más precisos y personalizados

### **💸 Endpoints de Ingresos - Cambios Implementados:**

#### **Todos los endpoints de ingresos** han recibido actualizaciones similares:

- `/api/incomes/` - CRUD con categorías por usuario
- `/api/incomes/summary/` - Resúmenes personalizados
- `/api/incomes/donut_chart_data/` - Gráficos con categorías del usuario
- `/api/incomes/bar_chart_data/` - Datos agrupados por usuario
- `/api/incomes/line_chart_data/` - Tendencias personalizadas
- `/api/incomes/stacked_bar_chart_data/` - Comparaciones por usuario
- `/api/incomes/statistics/` - Estadísticas basadas en categorías del usuario

---

## 📝 **Formato de Respuestas Detallado**

### **🏷️ Estructura de Categoría por Usuario:**

```json
{
  "id": 123,
  "name": "Transporte y Movilidad",
  "description": "Gastos relacionados con movilidad urbana",
  "examples": "Uber, taxi, gasolina, mantenimiento del carro", // Solo gastos
  "example": "Uber, trabajos de conductor", // Solo ingresos
  "color": "#FF5722",
  "is_default": true,
  "created_at": "2025-01-15T10:30:00Z",
  "updated_at": "2025-01-15T10:30:00Z"
}
```

### **💰 Respuesta de Gasto con Categorías Duales:**

```json
{
  "id": 456,
  "amount": 50000,
  "currency": "COP",
  "description": "Gasolina para el carro",
  "spent_at": "2025-01-15",
  "timestamp": "2025-01-15T15:30:00Z",

  // ✅ Campo nuevo (recomendado)
  "user_expense_category": {
    "id": 123,
    "name": "Transporte y Movilidad",
    "color": "#FF5722",
    "is_default": true
  },

  // 📱 Campo legacy (compatible)
  "category": {
    "id": 15,
    "name": "Transporte",
    "color": "#2196F3"
  },

  // 🎯 Campo calculado (prioriza usuario)
  "category_name": "Transporte y Movilidad"
}
```

### **📊 Respuesta de Estadísticas con Uso:**

```json
{
  "categories": [
    {
      "id": 123,
      "name": "Transporte y Movilidad",
      "color": "#FF5722",
      "description": "Gastos de movilidad urbana",
      "examples": "Uber, taxi, gasolina",
      "is_default": true,
      "usage_stats": {
        "usage_count": 15,
        "total_amount": 450000,
        "avg_amount": 30000,
        "last_used": "2025-01-15T10:30:00Z",
        "days_since_last_use": 2
      }
    }
  ],
  "totals": {
    "categories": 16,
    "predefined": 16,
    "custom": 0,
    "total_usage": 89
  }
}
```

### **🔍 Respuesta de Búsqueda:**

```json
{
  "results": [
    {
      "id": 123,
      "name": "Alimentación",
      "type": "expense",
      "match_score": 0.95,
      "color": "#4CAF50",
      "usage_count": 25
    }
  ],
  "total_results": 1,
  "search_query": "comida",
  "suggestion": "Quizás quisiste decir: 'Alimentación'"
}
```

### **📥 Respuesta de Importación:**

```json
{
  "status": "success",
  "imported": {
    "expense_categories": 5,
    "income_categories": 3
  },
  "skipped": {
    "duplicates": 2,
    "invalid": 0
  },
  "errors": [],
  "details": [
    {
      "name": "Nueva Categoría",
      "status": "created",
      "id": 789
    }
  ]
}
```

---

## 🛠️ **Guía de Implementación para el Frontend**

### **🔥 Estrategia Recomendada: Migración Gradual**

#### **Fase 1: Sin Cambios (Funciona Inmediatamente)**

```javascript
// ✅ Este código sigue funcionando exactamente igual
const getExpensesByCategory = async () => {
  const response = await fetch("/api/expenses/by_category/");
  const data = await response.json();

  // Los datos siguen teniendo la misma estructura
  return data.categories; // Funciona como antes
};
```

#### **Fase 2: Aprovechar Nuevas Características**

```javascript
// 🆕 Usar nuevos endpoints para mejor funcionalidad
const getUserCategories = async () => {
  const response = await fetch("/api/categories/expenses/");
  const categories = await response.json();

  // Acceso a categorías personalizadas del usuario
  return categories.filter((cat) => !cat.is_default);
};

// 🎨 Obtener colores personalizados
const getCategoryColors = async () => {
  const response = await fetch("/api/categories/expenses/colors_map/");
  return await response.json();
};

// 📊 Crear categoría con validación completa
const createExpenseCategory = async (categoryData) => {
  try {
    const response = await fetch("/api/categories/expenses/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        name: categoryData.name,
        description: categoryData.description,
        examples: categoryData.examples,
        color: categoryData.color,
      }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.name?.[0] || "Error al crear categoría");
    }

    return await response.json();
  } catch (error) {
    console.error("Error creando categoría:", error);
    throw error;
  }
};
```

#### **Fase 3: Funcionalidades Avanzadas**

```javascript
// 🚀 Crear categorías personalizadas con validación
const createCustomCategory = async (categoryData) => {
  return await fetch("/api/categories/expenses/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: categoryData.name,
      description: categoryData.description,
      examples: categoryData.examples,
      color: categoryData.color,
      is_default: false,
    }),
  });
};

// 📊 Estadísticas avanzadas con filtros
const getCategoriesWithUsage = async (days = 30) => {
  const response = await fetch(
    `/api/categories/expenses/with_usage/?days=${days}`
  );
  return await response.json();
};

// 🔍 Búsqueda inteligente
const searchCategories = async (query) => {
  const response = await fetch(
    `/api/categories/expenses/search/?q=${encodeURIComponent(query)}`
  );
  return await response.json();
};

// 📥 Importar categorías
const importCategories = async (file) => {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/categories/expenses/import/", {
    method: "POST",
    body: formData,
  });

  return await response.json();
};
```

### **🎯 Campos Recomendados para Usar**

#### **Para Gastos/Ingresos:**

```javascript
// ✅ Recomendado: Usar el campo híbrido
const categoryName = expense.category_name; // Prioriza usuario automáticamente

// 🆕 Avanzado: Acceso directo a categoría del usuario
if (expense.user_expense_category) {
  const userCategory = expense.user_expense_category;
  const color = userCategory.color;
  const isCustom = !userCategory.is_default;

  // Usar información rica de la categoría
  const description = userCategory.description;
  const examples = userCategory.examples;
}

// 📱 Legacy: Sigue funcionando para compatibilidad
const legacyCategory = expense.category?.name || "Sin categoría";
```

#### **Para Visualizaciones:**

```javascript
// 🎨 Usar colores personalizados del usuario
const getChartColors = async () => {
  const colorsMap = await fetch("/api/categories/expenses/colors_map/");
  return await colorsMap.json();
};

// 📊 Datos más precisos para gráficos
const chartData = expensesByCategory.categories_info.map((cat) => ({
  label: cat.name,
  value: cat.usage_stats?.total_amount || 0,
  color: cat.color,
  isCustom: !cat.is_default,
  usageCount: cat.usage_stats?.usage_count || 0,
}));
```

---

## 🔧 **Cambios Específicos por Componente**

### **📋 Gestión de Categorías**

#### **Lista de Categorías con Filtros:**

```javascript
// 🆕 Nuevo: Separar categorías predefinidas vs personalizadas
const loadCategories = async (includeUsage = false) => {
  const endpoint = includeUsage
    ? "/api/categories/expenses/with_usage/"
    : "/api/categories/expenses/";

  const response = await fetch(endpoint);
  const categories = await response.json();

  return {
    all: categories,
    defaults: categories.filter((cat) => cat.is_default),
    custom: categories.filter((cat) => !cat.is_default),
  };
};
```

#### **Editor de Categorías con Validación:**

```javascript
// 🆕 Crear categoría personalizada con manejo de errores
const CategoryEditor = () => {
  const [errors, setErrors] = useState({});

  const createCategory = async (formData) => {
    try {
      setErrors({});

      const response = await fetch("/api/categories/expenses/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: formData.name.trim(),
          description: formData.description,
          examples: formData.examples,
          color: formData.color || "#2196F3",
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        setErrors(errorData);
        return;
      }

      const newCategory = await response.json();
      // Éxito - categoría creada
      return newCategory;
    } catch (error) {
      setErrors({ general: "Error de conexión" });
    }
  };

  // Permitir edición solo de categorías no predefinidas
  const canEdit = (category) => !category.is_default;

  return (
    <form onSubmit={handleSubmit}>
      <input
        name="name"
        placeholder="Nombre de la categoría"
        required
        maxLength={100}
      />
      {errors.name && <span className="error">{errors.name[0]}</span>}

      <textarea
        name="description"
        placeholder="Descripción (opcional)"
        maxLength={500}
      />

      <textarea
        name="examples"
        placeholder="Ejemplos (opcional)"
        maxLength={500}
      />

      <input type="color" name="color" defaultValue="#2196F3" />

      <button type="submit">Crear Categoría</button>
    </form>
  );
};
```

### **📊 Dashboard y Gráficos**

#### **Gráfico de Gastos por Categoría Mejorado:**

```javascript
// ✅ Aprovecha automáticamente las mejoras del backend
const ExpensesPieChart = () => {
  const [data, setData] = useState(null);
  const [showCustomOnly, setShowCustomOnly] = useState(false);

  useEffect(() => {
    const endpoint = showCustomOnly
      ? "/api/expenses/by_category/?custom_only=true"
      : "/api/expenses/by_category/";

    fetch(endpoint)
      .then((res) => res.json())
      .then((data) => {
        // Los datos ya incluyen categorías del usuario automáticamente
        // Los colores ya son personalizados automáticamente
        setData({
          labels: data.categories,
          datasets: [
            {
              data: data.totals,
              backgroundColor: data.colors,
              borderWidth: 2,
            },
          ],
        });
      });
  }, [showCustomOnly]);

  return (
    <div>
      <label>
        <input
          type="checkbox"
          checked={showCustomOnly}
          onChange={(e) => setShowCustomOnly(e.target.checked)}
        />
        Solo categorías personalizadas
      </label>

      {data && <PieChart data={data} />}
    </div>
  );
};
```

#### **Estadísticas Avanzadas con Uso:**

```javascript
// 🆕 Aprovechar nuevas estadísticas
const CategoryUsageStats = () => {
  const [stats, setStats] = useState([]);
  const [period, setPeriod] = useState(30);

  useEffect(() => {
    fetch(`/api/categories/expenses/with_usage/?days=${period}`)
      .then((res) => res.json())
      .then((data) => {
        // Datos incluyen: usage_count, total_amount, avg_amount
        setStats(data.categories || []);
      });
  }, [period]);

  return (
    <div>
      <select value={period} onChange={(e) => setPeriod(e.target.value)}>
        <option value={7}>Última semana</option>
        <option value={30}>Último mes</option>
        <option value={90}>Últimos 3 meses</option>
      </select>

      {stats.map((category) => (
        <div key={category.id} className="category-stat">
          <h3 style={{ color: category.color }}>
            {category.name}
            {!category.is_default && " ★"}
          </h3>
          <p>Usado {category.usage_stats?.usage_count || 0} veces</p>
          <p>Total: ${category.usage_stats?.total_amount || 0}</p>
          <p>Promedio: ${category.usage_stats?.avg_amount || 0}</p>
        </div>
      ))}
    </div>
  );
};
```

### **📝 Formularios de Gastos/Ingresos**

#### **Selector de Categorías Inteligente:**

```javascript
// 🔄 Mejorado: Mostrar categorías del usuario primero
const CategorySelector = ({ onSelect, type = "expense" }) => {
  const [categories, setCategories] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [filteredCategories, setFilteredCategories] = useState([]);

  useEffect(() => {
    const endpoint =
      type === "expense"
        ? "/api/categories/expenses/"
        : "/api/categories/incomes/";

    fetch(endpoint)
      .then((res) => res.json())
      .then((data) => {
        // Ordenar: personalizadas primero, luego predefinidas
        const sorted = data.sort((a, b) => {
          if (a.is_default !== b.is_default) {
            return a.is_default ? 1 : -1; // Personalizadas primero
          }
          return a.name.localeCompare(b.name);
        });
        setCategories(sorted);
        setFilteredCategories(sorted);
      });
  }, [type]);

  // Búsqueda en tiempo real
  useEffect(() => {
    if (!searchQuery) {
      setFilteredCategories(categories);
      return;
    }

    const filtered = categories.filter(
      (cat) =>
        cat.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        cat.description?.toLowerCase().includes(searchQuery.toLowerCase())
    );
    setFilteredCategories(filtered);
  }, [searchQuery, categories]);

  const createNewCategory = async () => {
    if (!searchQuery.trim()) return;

    try {
      const endpoint =
        type === "expense"
          ? "/api/categories/expenses/"
          : "/api/categories/incomes/";

      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: searchQuery.trim(),
          description: `Categoría personalizada: ${searchQuery}`,
          [type === "expense" ? "examples" : "example"]: "",
          color: "#2196F3",
        }),
      });

      if (response.ok) {
        const newCategory = await response.json();
        setCategories((prev) => [newCategory, ...prev]);
        onSelect(newCategory);
        setSearchQuery("");
      }
    } catch (error) {
      console.error("Error creando categoría:", error);
    }
  };

  return (
    <div className="category-selector">
      <input
        type="text"
        placeholder="Buscar o crear categoría..."
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />

      <div className="categories-list">
        {filteredCategories.map((cat) => (
          <div
            key={cat.id}
            className="category-option"
            onClick={() => onSelect(cat)}
          >
            <span
              className="color-indicator"
              style={{ backgroundColor: cat.color }}
            />
            <span className="name">
              {cat.name} {!cat.is_default && "★"}
            </span>
            {cat.description && (
              <span className="description">{cat.description}</span>
            )}
          </div>
        ))}

        {searchQuery && filteredCategories.length === 0 && (
          <div className="create-new" onClick={createNewCategory}>
            + Crear "{searchQuery}"
          </div>
        )}
      </div>
    </div>
  );
};
```

---

## ⚠️ **Validaciones y Errores**

### **🔒 Validaciones del Backend:**

**Campos requeridos:**

- `name`: Requerido, máximo 100 caracteres, único por usuario
- `color`: Formato hexadecimal válido (#RRGGBB)
- `description`: Opcional, máximo 500 caracteres
- `examples/example`: Opcional, máximo 500 caracteres

**Errores comunes:**

```json
{
  "name": ["Ya tienes una categoría de gasto llamada 'Transporte'"],
  "color": ["Ingresa un color válido en formato hexadecimal"],
  "description": ["La descripción no puede exceder 500 caracteres"]
}
```

### **🚫 Restricciones:**

- No se pueden eliminar categorías predefinidas (`is_default: true`)
- No se pueden crear categorías con nombres duplicados
- Los colores deben ser códigos hexadecimales válidos
- Las categorías deben tener al menos un carácter en el nombre

---

## 📈 **Performance y Optimización**

### **🚀 Recomendaciones:**

**Cachear categorías:**

```javascript
// Cache de categorías en localStorage
const getCachedCategories = (type, cacheTime = 5 * 60 * 1000) => {
  const cacheKey = `categories_${type}`;
  const cached = localStorage.getItem(cacheKey);

  if (cached) {
    const { data, timestamp } = JSON.parse(cached);
    if (Date.now() - timestamp < cacheTime) {
      return data;
    }
  }
  return null;
};

const setCachedCategories = (type, data) => {
  const cacheKey = `categories_${type}`;
  localStorage.setItem(
    cacheKey,
    JSON.stringify({
      data,
      timestamp: Date.now(),
    })
  );
};
```

**Paginación para listas grandes:**

```javascript
GET /api/categories/expenses/?page=1&limit=20
```

**Debounce para búsquedas:**

```javascript
const debouncedSearch = useCallback(
  debounce((query) => {
    fetch(`/api/categories/expenses/search/?q=${query}`)
      .then((res) => res.json())
      .then(setSearchResults);
  }, 300),
  []
);
```

---

## 🚀 **Beneficios de la Migración**

### **Para los Usuarios:**

- **🎯 Personalización:** Categorías adaptadas a sus necesidades
- **🎨 Colores:** Personalización visual de categorías
- **📊 Precisión:** Estadísticas más relevantes y exactas
- **🔧 Control:** Gestión completa de sus categorías

### **Para el Frontend:**

- **🔌 APIs Poderosas:** 36 nuevos endpoints especializados
- **📈 Datos Mejorados:** Información más rica y detallada
- **🎨 Personalización:** Soporte nativo para temas del usuario
- **🔄 Compatibilidad:** Sin romper funcionalidad existente

---

## 📞 **Soporte y Migración**

### **🆘 ¿Necesitas Ayuda?**

- **📖 Documentación:** Este documento cubre todos los casos
- **🧪 Testing:** Usar endpoints en modo de prueba
- **🔄 Rollback:** Compatibilidad garantiza retorno seguro

### **📋 Checklist de Migración:**

- [ ] Probar endpoints existentes (deben funcionar igual)
- [ ] Implementar gestión básica de categorías
- [ ] Aprovechar nuevos campos en respuestas
- [ ] Añadir funcionalidades de personalización
- [ ] Actualizar visualizaciones con colores del usuario
- [ ] Implementar creación/edición de categorías personalizadas
- [ ] Agregar validación de formularios
- [ ] Implementar búsqueda y filtros
- [ ] Optimizar con cacheo y paginación

---

**🎉 ¡El sistema está listo para usar! El frontend puede comenzar a aprovechar las nuevas funcionalidades de inmediato mientras mantiene toda la compatibilidad existente.**
