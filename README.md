# CashBot API

API REST para la gestión de finanzas personales con integración de bots de Telegram y WhatsApp, desarrollada con Django y PostgreSQL con soporte para vectores.

## 🚀 Ejecución Local con Docker

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

#### 🐘 Base de Datos (PostgreSQL + pgvector)

- **Imagen**: `ankane/pgvector`
- **Puerto**: 5433
- **Credenciales**:
  - Usuario: `cashbot`
  - Contraseña: `cashbot`
  - Base de datos: `cashbot`

#### 🐍 Aplicación Web (Django)

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

### Tecnologías Principales

- **Backend**: Django 5.2 + Django REST Framework 
- **Base de Datos**: PostgreSQL con extensión pgvector
- **Autenticación**: JWT con SimpleJWT
- **IA**: LangChain + OpenAI
- **Bots**: python-telegram-bot
- **Documentación**: drf-spectacular (OpenAPI/Swagger)
