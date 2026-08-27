"""Configuración de gunicorn para producción (Railway).

gunicorn lee este archivo solo si está en el directorio de trabajo, así que
aplica aunque el startCommand del servicio sea un `gunicorn cashbotapp.wsgi`
sin flags (que es el caso en Railway y anula al Procfile). Los flags de línea
de comandos, si los hubiera, siguen teniendo prioridad sobre estos valores.

Por qué gthread y 180 s: el chat de agentes (/api/agents/*/chat/stream/) es
un stream SSE que encadena varias llamadas al modelo y supera con facilidad
los 30 s del timeout por defecto. Con el worker sync, el heartbeat al arbiter
solo se envía entre peticiones, así que cualquier stream largo terminaba en
WORKER TIMEOUT + SIGKILL a mitad de respuesta. Con gthread el hilo principal
sigue latiendo mientras los hilos atienden peticiones largas.
"""

import os

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
worker_class = "gthread"
workers = int(os.getenv("GUNICORN_WORKERS", "2"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "180"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
