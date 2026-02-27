# CashBot API - Backend Inteligente

API REST para la gestión de finanzas personales potenciada por Inteligencia Artificial, con integración de bots de Telegram y WhatsApp, desarrollada con Django y PostgreSQL con soporte para vectores.

## Features de IA del Backend

### Procesamiento de Lenguaje Natural (NLP)
- Comprensión automática de mensajes de usuarios en lenguaje natural
- Extracción inteligente de información de gastos e ingresos
- Análisis de contexto para categorización automática
- Manejo de múltiples formatos de entrada de datos

### Integración con OpenAI y LangChain
- Procesamiento avanzado con modelos de IA generativa
- Cadenas de procesamiento inteligentes (LangChain)
- Análisis semántico de transacciones
- Generación de reportes automáticos

### Bot de Telegram Inteligente
- Conversación natural con asistente financiero
- Registro automático de gastos mediante mensaje
- Categorización inteligente de transacciones
- Análisis de patrones de gasto en tiempo real
- Alertas personalizadas sobre límites de gasto
- Generación de reportes financieros automáticos

### Vector Database (pgvector)
- Almacenamiento de embeddings para búsqueda semántica
- Recuperación de información relevante basada en similitud
- Mejora de recomendaciones personalizadas
- Análisis de patrones históricos

### Categorización Automática
- Clasificación inteligente de transacciones
- Aprendizaje de patrones de usuario
- Categorías personalizadas y predefinidas
- Actualización automática de categorías basada en historial

### Análisis Financiero Avanzado
- Cálculo automático de estadísticas
- Detección de patrones de gasto
- Proyecciones de ahorro
- Análisis comparativo de períodos

## Ejecución Local con Docker

### Prerrequisitos

- Docker
- Docker Compose
- 

### Configuración y Ejecución

1. **Clonar el repositorio**

   ```bash
   git clone <url-del-repositorio>
   cd cashbot-api
   ```

2. **Construir y ejecutar los contenedores**

   ```bash
   docker-compose -f docker-compose.dev.yml up --build
   ```

   Este comando:

   - Construye la imagen de la aplicación usando `Dockerfile.dev`
   - Levanta una base de datos PostgreSQL con extensión pgvector
   - Ejecuta las migraciones automáticamente
   - Inicia el servidor de desarrollo en el puerto 8000

3. **Acceder a la aplicación**
   - API: http://localhost:8000
   - Base de datos PostgreSQL: localhost:5433

### Servicios Incluidos

#### Base de Datos (PostgreSQL + pgvector)

- **Imagen**: `ankane/pgvector`
- **Puerto**: 5433
- **Credenciales**:
  - Usuario: `cashbot`
  - Contraseña: `cashbot`
  - Base de datos: `cashbot`

#### Aplicación Web (Django)

- **Puerto**: 8000
- **Modo**: Desarrollo con DEBUG=True
- **Recarga automática**: Habilitada mediante volumen montado

### Comandos Útiles

```bash
# Ejecutar en segundo plano
docker-compose -f docker-compose.dev.yml up -d

# Ver logs
docker-compose -f docker-compose.dev.yml logs -f

# Parar los servicios
docker-compose -f docker-compose.dev.yml down

# Parar y eliminar volúmenes (resetear BD)
docker-compose -f docker-compose.dev.yml down -v

# Ejecutar comandos Django dentro del contenedor
docker-compose -f docker-compose.dev.yml exec web python manage.py <comando>

# Crear superusuario
docker-compose -f docker-compose.dev.yml exec web python manage.py createsuperuser

# Ejecutar shell de Django
docker-compose -f docker-compose.dev.yml exec web python manage.py shell
```

### Estructura del Proyecto

- `Dockerfile.dev`: Imagen de desarrollo con Python 3.13
- `docker-compose.dev.yml`: Orquestación de servicios para desarrollo
- `requirements.txt`: Dependencias de Python incluyendo Django, DRF, pgvector, etc.
- `telegrambot/`: Integración del bot de Telegram con IA
  - `bot.py`: Lógica principal del bot conversacional
  - `services.py`: Procesamiento inteligente de mensajes
  - `tools.py`: Herramientas de IA y utilidades
  - `utils.py`: Funciones auxiliares de NLP
- `expenses/`: Gestión inteligente de gastos
- `income/`: Análisis de ingresos
- `categories/`: Categorización automática de transacciones
- `savings/`: Análisis y metas de ahorro
- `whatsappbot/`: Integración del bot de WhatsApp

### Tecnologías Principales

- **Backend**: Django 5.2 + Django REST Framework
- **Base de Datos**: PostgreSQL con extensión pgvector
- **Autenticación**: JWT con SimpleJWT
- **Inteligencia Artificial**:
  - LangChain: Cadenas de procesamiento inteligente
  - OpenAI: Modelos de IA generativa
  - pgvector: Vector embeddings para búsqueda semántica
- **Bots**: python-telegram-bot para integración conversacional
- **Documentación**: drf-spectacular (OpenAPI/Swagger)
