# Tresqu API

**Backend de gestion financiera personal con IA**

Django REST API para Tresqu. Incluye gestion de gastos/ingresos, chatbots inteligentes (Telegram + WhatsApp), deteccion automatica de compras via Gmail, y analisis financiero con IA.

## Tech Stack

- **Python 3.13** + **Django 5.2** + **Django REST Framework**
- **PostgreSQL + pgvector** — base de datos relacional con soporte de embeddings vectoriales
- **LangChain + OpenAI GPT-4.1** — pipeline de IA para NLP y analisis
- **python-telegram-bot** — integracion con Telegram
- **Meta WhatsApp API** — integracion con WhatsApp Business
- **Google Gmail API** — deteccion automatica de compras en correos
- **Gunicorn** — servidor WSGI para produccion

## Requisitos previos

- Docker y Docker Compose
- Python 3.13+ (solo si se ejecuta sin Docker)

## Instalacion rapida

```bash
# Clonar el repositorio
git clone <repo-url>
cd cashbot-api

# Copiar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales

# Levantar con Docker
docker-compose -f docker-compose.dev.yml up --build

# Correr migraciones
docker-compose -f docker-compose.dev.yml exec web python manage.py migrate

# Crear superusuario (opcional)
docker-compose -f docker-compose.dev.yml exec web python manage.py createsuperuser
```

La API estara disponible en `http://localhost:8000`.

## Variables de entorno

Consulta el archivo [`.env.example`](.env.example) para ver todas las variables requeridas, agrupadas por seccion. Copia el archivo como `.env` y completa los valores antes de levantar los servicios.

## Estructura del proyecto

| App | Responsabilidad |
|-----|----------------|
| `cashbotapp/` | Settings del proyecto, URLs raiz, autenticacion JWT, middleware |
| `users/` | Auth, JWT, perfiles de usuario, planes de suscripcion, referidos |
| `expenses/` | CRUD de gastos + analytics, embeddings con pgvector para busqueda semantica |
| `income/` | CRUD de ingresos + analytics |
| `categories/` | Categorias predefinidas y personalizadas por usuario |
| `savings/` | Metas de ahorro y proyecciones financieras |
| `telegrambot/` | Bot de Telegram — NLP, IA, extraccion automatica de transacciones |
| `whatsappbot/` | Bot de WhatsApp — Meta API, soporte de voz, imagenes y registro de usuarios |
| `gmailbot/` | Integracion Gmail — OAuth2, deteccion de compras, categorizacion via WhatsApp |

## Endpoints principales

| Endpoint | Descripcion |
|----------|-------------|
| `/api/expenses/` | CRUD de gastos |
| `/api/incomes/` | CRUD de ingresos |
| `/api/categories/` | Categorias de transacciones |
| `/api/savings/` | Metas de ahorro |
| `/api/users/` | Usuarios y autenticacion |
| `/api/token/` | Obtener tokens JWT |
| `/api/token/refresh/` | Refrescar token JWT |
| `/api/gmail/` | Integracion Gmail (OAuth, sync, estado) |
| `/telegram/` | Webhook de Telegram |
| `/whatsapp/` | Webhook de WhatsApp |
| `/gmail/webhook/` | Webhook de Gmail Pub/Sub |
| `/schema/swagger-ui/` | Documentacion interactiva Swagger UI |
| `/schema/redoc/` | Documentacion ReDoc |

## Integracion Gmail

Deteccion automatica de compras en correos electronicos:

1. El usuario conecta su cuenta de Gmail via OAuth2
2. Gmail Watch + Pub/Sub detecta correos nuevos en tiempo real
3. La IA analiza si el correo corresponde a una compra
4. Si es una compra, se crea el gasto automaticamente y se pregunta la categoria por WhatsApp
5. El usuario responde con la categoria y el gasto queda categorizado

### Management commands

```bash
# Renovar watches de Gmail que estan por expirar
python manage.py renew_gmail_watches

# Sincronizacion manual para todos los usuarios
python manage.py gmail_manual_sync --all

# Sincronizacion manual para un usuario especifico
python manage.py gmail_manual_sync --user_id 31
```

Para mas detalles:
- Arquitectura y modulos: [`gmailbot/README.md`](gmailbot/README.md)
- **Guia de configuracion paso a paso**: [`docs/GMAIL_SETUP_GUIDE.md`](docs/GMAIL_SETUP_GUIDE.md)

## Documentacion API

La documentacion de la API se genera automaticamente con `drf-spectacular`:

- **Swagger UI**: `http://localhost:8000/schema/swagger-ui/`
- **ReDoc**: `http://localhost:8000/schema/redoc/`

## Servicios Docker

| Servicio | Puerto | Descripcion |
|----------|--------|-------------|
| `web` | 8000 | Django API (Gunicorn en produccion) |
| `db` | 5433 | PostgreSQL + pgvector |

### Comandos utiles de Docker

```bash
# Levantar en segundo plano
docker-compose -f docker-compose.dev.yml up -d

# Ver logs
docker-compose -f docker-compose.dev.yml logs -f

# Detener servicios
docker-compose -f docker-compose.dev.yml down

# Resetear base de datos (elimina volumenes)
docker-compose -f docker-compose.dev.yml down -v

# Ejecutar comandos de Django dentro del contenedor
docker-compose -f docker-compose.dev.yml exec web python manage.py migrate
docker-compose -f docker-compose.dev.yml exec web python manage.py shell
```

## Licencia

Proyecto privado — Tresqu
