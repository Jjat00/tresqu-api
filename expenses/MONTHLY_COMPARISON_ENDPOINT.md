# Endpoint: Monthly Comparison Chart Data

## Descripción

Este endpoint proporciona datos para crear una gráfica que compara los ingresos mensuales con los gastos acumulados día a día. Es ideal para visualizar qué tan cerca está el usuario de superar sus ingresos durante el mes.

## URL

```
GET /api/expenses/monthly_comparison_chart_data/
```

## Parámetros

| Parámetro  | Tipo    | Requerido | Descripción              | Valor por defecto |
| ---------- | ------- | --------- | ------------------------ | ----------------- |
| `month`    | integer | No        | Mes a analizar (1-12)    | Mes actual        |
| `year`     | integer | No        | Año a analizar           | Año actual        |
| `timezone` | string  | No        | Zona horaria del usuario | 'America/Bogota'  |

## Ejemplo de petición

```bash
curl -X GET "http://localhost:8000/api/expenses/monthly_comparison_chart_data/?month=12&year=2024" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

## Respuesta exitosa (200 OK)

```json
{
  "labels": [
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "20",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31"
  ],
  "datasets": [
    {
      "label": "Ingresos del mes",
      "data": [
        5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0,
        5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0,
        5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0, 5000.0,
        5000.0, 5000.0, 5000.0, 5000.0
      ],
      "borderColor": "#4CAF50",
      "backgroundColor": "rgba(76, 175, 80, 0.1)",
      "borderWidth": 3,
      "fill": false,
      "type": "line",
      "tension": 0
    },
    {
      "label": "Gastos acumulados",
      "data": [
        150.0, 280.0, 420.0, 650.0, 850.0, 1200.0, 1350.0, 1580.0, 1750.0,
        2100.0, 2300.0, 2650.0, 2850.0, 3200.0, 3450.0, 3750.0, 4000.0, 4350.0,
        4600.0, 4850.0, 5100.0, 5400.0, 5650.0, 5900.0, 6200.0, 6450.0, 6700.0,
        6950.0, 7200.0, 7450.0, 7700.0
      ],
      "borderColor": "#F44336",
      "backgroundColor": "rgba(244, 67, 54, 0.1)",
      "borderWidth": 3,
      "fill": true,
      "type": "line",
      "tension": 0.1
    }
  ],
  "month_info": {
    "month": 12,
    "year": 2024,
    "month_name": "December",
    "total_days": 31
  },
  "financial_summary": {
    "total_monthly_income": 5000.0,
    "total_expenses_to_date": 7700.0,
    "remaining_budget": -2700.0,
    "percentage_consumed": 154.0,
    "financial_status": "crítico",
    "days_to_exceed_income": 21
  },
  "chart_config": {
    "type": "line",
    "responsive": true,
    "scales": {
      "y": {
        "beginAtZero": true,
        "title": {
          "display": true,
          "text": "Monto ($)"
        }
      },
      "x": {
        "title": {
          "display": true,
          "text": "Días del mes (December 2024)"
        }
      }
    }
  }
}
```

## Campos de respuesta

### `labels`

Array de strings con los días del mes (formato "01", "02", etc.)

### `datasets`

Array con dos objetos:

1. **Ingresos del mes**: Línea constante con el total de ingresos
2. **Gastos acumulados**: Línea que muestra la suma acumulada día a día

### `month_info`

- `month`: Número del mes (1-12)
- `year`: Año
- `month_name`: Nombre del mes en inglés
- `total_days`: Total de días en el mes

### `financial_summary`

- `total_monthly_income`: Total de ingresos del mes
- `total_expenses_to_date`: Total de gastos acumulados hasta la fecha
- `remaining_budget`: Presupuesto restante (puede ser negativo)
- `percentage_consumed`: Porcentaje de ingresos consumido
- `financial_status`: Estado financiero ("saludable", "precaución", "advertencia", "crítico")
- `days_to_exceed_income`: Día en que se superaron los ingresos (null si no ha ocurrido)

### `chart_config`

Configuración recomendada para Chart.js

## Estados financieros

| Estado        | Descripción             | Porcentaje consumido |
| ------------- | ----------------------- | -------------------- |
| `saludable`   | Gastos bajo control     | < 60%                |
| `precaución`  | Gastos moderados        | 60% - 79%            |
| `advertencia` | Gastos altos            | 80% - 99%            |
| `crítico`     | Gastos superan ingresos | ≥ 100%               |

## Ejemplo de uso con Chart.js

```javascript
// Hacer petición al endpoint
fetch("/api/expenses/monthly_comparison_chart_data/?month=12&year=2024", {
  headers: {
    Authorization: "Bearer " + token,
  },
})
  .then((response) => response.json())
  .then((data) => {
    // Crear gráfica con Chart.js
    const ctx = document
      .getElementById("monthlyComparisonChart")
      .getContext("2d");

    new Chart(ctx, {
      type: "line",
      data: {
        labels: data.labels,
        datasets: data.datasets,
      },
      options: {
        responsive: true,
        interaction: {
          mode: "index",
          intersect: false,
        },
        scales: data.chart_config.scales,
        plugins: {
          title: {
            display: true,
            text: `Comparación Ingresos vs Gastos - ${data.month_info.month_name} ${data.month_info.year}`,
          },
          legend: {
            display: true,
            position: "top",
          },
        },
      },
    });

    // Mostrar resumen financiero
    document.getElementById("financial-status").textContent =
      data.financial_summary.financial_status;
    document.getElementById("percentage-consumed").textContent =
      data.financial_summary.percentage_consumed + "%";
    document.getElementById("remaining-budget").textContent =
      "$" + data.financial_summary.remaining_budget;
  });
```

## Ejemplo de uso con React

```jsx
import React, { useState, useEffect } from "react";
import { Line } from "react-chartjs-2";

const MonthlyComparisonChart = ({ month, year }) => {
  const [chartData, setChartData] = useState(null);
  const [financialSummary, setFinancialSummary] = useState(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await fetch(
          `/api/expenses/monthly_comparison_chart_data/?month=${month}&year=${year}`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
            },
          }
        );

        const data = await response.json();

        setChartData({
          labels: data.labels,
          datasets: data.datasets,
        });

        setFinancialSummary(data.financial_summary);
      } catch (error) {
        console.error("Error fetching chart data:", error);
      }
    };

    fetchData();
  }, [month, year]);

  if (!chartData) return <div>Cargando...</div>;

  return (
    <div>
      <Line
        data={chartData}
        options={{
          responsive: true,
          scales: {
            y: {
              beginAtZero: true,
              title: {
                display: true,
                text: "Monto ($)",
              },
            },
          },
        }}
      />

      {financialSummary && (
        <div className="financial-summary">
          <h3>Resumen Financiero</h3>
          <p>
            Estado:{" "}
            <span className={`status-${financialSummary.financial_status}`}>
              {financialSummary.financial_status}
            </span>
          </p>
          <p>Porcentaje consumido: {financialSummary.percentage_consumed}%</p>
          <p>Presupuesto restante: ${financialSummary.remaining_budget}</p>
          {financialSummary.days_to_exceed_income && (
            <p>
              Día que se superaron los ingresos:{" "}
              {financialSummary.days_to_exceed_income}
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default MonthlyComparisonChart;
```

## Errores posibles

### 400 Bad Request

```json
{
  "error": "El mes debe estar entre 1 y 12"
}
```

```json
{
  "error": "Mes y año deben ser números enteros"
}
```

### 401 Unauthorized

El usuario no está autenticado. Incluir token JWT en el header Authorization.

## Notas importantes

1. **Autenticación requerida**: El endpoint requiere un token JWT válido
2. **Zona horaria**: Los cálculos se realizan en la zona horaria especificada
3. **Datos acumulados**: Los gastos se muestran de forma acumulativa día a día
4. **Línea constante**: Los ingresos se muestran como una línea horizontal constante
5. **Estado financiero**: Se calcula automáticamente basado en el porcentaje de ingresos consumido
