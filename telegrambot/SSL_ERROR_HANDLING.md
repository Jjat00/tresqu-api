# SSL Error Handling - Telegram Bot

## Problema

El bot ocasionalmente experimenta errores de tipo `SSL SYSCALL error: EOF detected` cuando se conecta a la API de OpenAI. Este error ocurre cuando la conexión SSL se cierra inesperadamente durante una solicitud.

## Solución Implementada

### 1. Configuración Centralizada (`config.py`)

- Timeouts configurables para requests de OpenAI
- Número máximo de reintentos
- Patrones de errores que se pueden reintentar
- Mensajes de error personalizados

### 2. Retry Logic con Backoff Exponencial (`services.py`)

- Función `retry_with_backoff()` que reintenta automáticamente en caso de errores SSL/conexión
- Backoff exponencial con jitter para evitar sobrecarga del servidor
- Detección inteligente de errores que se pueden reintentar

### 3. Configuración Mejorada de Clientes OpenAI

- **ChatOpenAI**: Configurado con timeout de 60s y 3 reintentos automáticos
- **OpenAI Client**: Configurado con timeout de 60s para transcripciones
- **Embeddings**: Configurado con timeout y reintentos

### 4. Monitoreo y Logging Detallado

- Función `log_ssl_error_details()` para logging detallado de errores SSL
- Categorización automática de tipos de errores
- Healthcheck endpoint en `/health/` para monitorear conectividad

### 5. Manejo de Errores en el Agente

- `AgentExecutor` configurado con:
  - `handle_parsing_errors=True`
  - `max_execution_time=120s`
  - `max_iterations=10`

## Errores Manejados

- `SSL SYSCALL error: EOF detected`
- `HTTPSConnectionPool timeout`
- `RemoteProtocolError`
- `ConnectionError`
- `SSLError`

## Monitoreo

### Healthcheck Endpoint

```
GET /telegrambot/health/
```

Respuesta ejemplo:

```json
{
  "status": "healthy",
  "timestamp": "2025-01-17T23:48:20.705Z",
  "checks": {
    "telegram_token": true,
    "openai_key": true,
    "webhook_url": true,
    "bot_initialized": true,
    "event_loop_active": true,
    "openai_client": true
  }
}
```

### Logs Detallados

Los errores SSL se registran con información detallada:

```json
{
  "timestamp": "2025-01-17T23:48:20.705Z",
  "context": "Retry attempt 3/3 failed",
  "error_type": "SSLError",
  "error_message": "SSL SYSCALL error: EOF detected",
  "category": "SSL_EOF"
}
```

## Configuración

### Variables de Entorno Requeridas

- `OPENAI_API_KEY`: Clave de la API de OpenAI
- `TELEGRAM_BOT_TOKEN`: Token del bot de Telegram
- `TELEGRAM_WEBHOOK_URL`: URL del webhook

### Configuración en `config.py`

```python
# Timeouts (en segundos)
OPENAI_REQUEST_TIMEOUT = 60
OPENAI_MAX_RETRIES = 3
AGENT_EXECUTION_TIMEOUT = 120

# Reintentos
RETRY_BASE_DELAY = 1  # Delay inicial
RETRY_MAX_DELAY = 30  # Delay máximo
RETRY_MAX_ATTEMPTS = 3  # Número máximo de reintentos
```

## Próximos Pasos

1. **Monitoreo en Producción**: Integrar con servicios como Sentry o DataDog
2. **Circuit Breaker**: Implementar circuit breaker para evitar cascada de errores
3. **Cache**: Implementar cache para reducir llamadas a OpenAI
4. **Load Balancing**: Distribuir carga entre múltiples instancias
