"""Retry utilities shared across agent invocations.

Lifted from ``telegrambot/services.py`` so both Telegram and WhatsApp paths
go through the same backoff + SSL handling.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from telegrambot.config import (
    RETRY_BASE_DELAY,
    RETRY_MAX_ATTEMPTS,
    RETRY_MAX_DELAY,
    RETRYABLE_ERROR_PATTERNS,
)
from telegrambot.utils import log_ssl_error_details

logger = logging.getLogger(__name__)


async def retry_with_backoff(func, max_retries=None, base_delay=None, max_delay=None, *args, **kwargs):
    """Run ``func`` with exponential backoff on retryable network/SSL errors."""

    max_retries = max_retries or RETRY_MAX_ATTEMPTS
    base_delay = base_delay or RETRY_BASE_DELAY
    max_delay = max_delay or RETRY_MAX_DELAY

    for attempt in range(max_retries):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)
        except Exception as exc:
            error_str = str(exc).lower()
            is_retryable = any(p in error_str for p in RETRYABLE_ERROR_PATTERNS)

            if not is_retryable or attempt == max_retries - 1:
                if is_retryable:
                    log_ssl_error_details(
                        exc, f"Retry attempt {attempt + 1}/{max_retries} failed"
                    )
                logger.error(f"Error no recuperable o último intento: {exc}")
                raise

            delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
            logger.warning(
                f"Error de conexión (intento {attempt + 1}/{max_retries}): {exc}. "
                f"Reintentando en {delay:.2f}s"
            )

            if asyncio.iscoroutinefunction(func):
                await asyncio.sleep(delay)
            else:
                time.sleep(delay)
