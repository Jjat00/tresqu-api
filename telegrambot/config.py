# telegrambot/config.py
"""
Configuración centralizada para timeouts, reintentos y manejo de errores
"""

# Configuración de timeouts (en segundos)
OPENAI_REQUEST_TIMEOUT = 60
OPENAI_MAX_RETRIES = 3
AGENT_EXECUTION_TIMEOUT = 120
AGENT_MAX_ITERATIONS = 10

# Configuración de reintentos con backoff exponencial
RETRY_BASE_DELAY = 1  # Delay inicial en segundos
RETRY_MAX_DELAY = 30  # Delay máximo en segundos
RETRY_MAX_ATTEMPTS = 3  # Número máximo de reintentos

# Patrones de errores que se pueden reintentar
RETRYABLE_ERROR_PATTERNS = [
    'ssl',
    'connection',
    'timeout',
    'eof detected',
    'remote disconnected',
    'network',
    'httpx.remoteprotocolerror',
    'httpx.connecterror',
    'httpx.timeouterror',
    'connectionerror',
    'httpsconnectionpool',
    'max retries exceeded'
]

# Mensajes de error personalizados
ERROR_MESSAGES = {
    'timeout': "Lo siento, la operación tomó demasiado tiempo. Por favor, intenta de nuevo con un mensaje más corto o específico.",
    'connection': "Hay un problema temporal de conexión. Por favor, intenta de nuevo en unos momentos.",
    'ssl': "Hay un problema temporal de conexión segura. Por favor, intenta de nuevo en unos momentos.",
    'default': "Lo siento, hubo un error al procesar tu mensaje. Por favor, intenta de nuevo."
}
 