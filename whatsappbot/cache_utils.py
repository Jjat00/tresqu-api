"""
Utilidades de caché con respaldo para WhatsApp Bot
Proporciona un sistema de caché robusto que puede usar tanto
el caché de Django (base de datos) como un respaldo en memoria local.
"""
import logging
import time
from typing import Any, Optional
from django.core.cache import cache as django_cache
from django.core.cache.backends.base import InvalidCacheBackendError

logger = logging.getLogger(__name__)

# Cache en memoria como respaldo
_memory_cache = {}
_cache_timestamps = {}

# Configuración
MEMORY_CACHE_MAX_SIZE = 1000  # Máximo número de entradas en memoria
MEMORY_CACHE_DEFAULT_TIMEOUT = 300  # 5 minutos por defecto


def _cleanup_memory_cache():
    """Limpia entradas expiradas del caché en memoria"""
    current_time = time.time()
    expired_keys = []

    for key, timestamp in _cache_timestamps.items():
        if current_time > timestamp:
            expired_keys.append(key)

    for key in expired_keys:
        _memory_cache.pop(key, None)
        _cache_timestamps.pop(key, None)

    # Si aún hay demasiadas entradas, eliminar las más antiguas
    if len(_memory_cache) > MEMORY_CACHE_MAX_SIZE:
        # Ordenar por timestamp y eliminar las más antiguas
        sorted_keys = sorted(_cache_timestamps.items(), key=lambda x: x[1])
        keys_to_remove = sorted_keys[:len(
            _memory_cache) - MEMORY_CACHE_MAX_SIZE]

        for key, _ in keys_to_remove:
            _memory_cache.pop(key, None)
            _cache_timestamps.pop(key, None)


def set_cache(key: str, value: Any, timeout: int = MEMORY_CACHE_DEFAULT_TIMEOUT) -> bool:
    """
    Establece un valor en el caché con respaldo en memoria

    Args:
        key: Clave del caché
        value: Valor a almacenar
        timeout: Tiempo de expiración en segundos

    Returns:
        bool: True si se almacenó exitosamente
    """
    try:
        # Intentar usar el caché de Django primero
        django_cache.set(key, value, timeout)
        logger.debug(f"✅ Valor almacenado en caché Django: {key}")
        return True

    except Exception as e:
        logger.warning(f"⚠️ Error en caché Django, usando memoria: {e}")

        try:
            # Usar caché en memoria como respaldo
            _cleanup_memory_cache()

            _memory_cache[key] = value
            _cache_timestamps[key] = time.time() + timeout

            logger.debug(f"✅ Valor almacenado en caché memoria: {key}")
            return True

        except Exception as mem_error:
            logger.error(f"❌ Error en ambos cachés: {mem_error}")
            return False


def get_cache(key: str) -> Optional[Any]:
    """
    Obtiene un valor del caché con respaldo en memoria

    Args:
        key: Clave del caché

    Returns:
        Optional[Any]: Valor del caché o None si no existe/expiró
    """
    try:
        # Intentar usar el caché de Django primero
        value = django_cache.get(key)
        if value is not None:
            logger.debug(f"✅ Valor encontrado en caché Django: {key}")
            return value

    except Exception as e:
        logger.warning(f"⚠️ Error en caché Django, verificando memoria: {e}")

    try:
        # Usar caché en memoria como respaldo
        _cleanup_memory_cache()

        if key in _memory_cache:
            current_time = time.time()
            expiry_time = _cache_timestamps.get(key, 0)

            if current_time <= expiry_time:
                logger.debug(f"✅ Valor encontrado en caché memoria: {key}")
                return _memory_cache[key]
            else:
                # Valor expirado
                _memory_cache.pop(key, None)
                _cache_timestamps.pop(key, None)

    except Exception as mem_error:
        logger.error(f"❌ Error en caché memoria: {mem_error}")

    logger.debug(f"❌ Valor no encontrado en ningún caché: {key}")
    return None


def delete_cache(key: str) -> bool:
    """
    Elimina un valor del caché en ambos sistemas

    Args:
        key: Clave del caché

    Returns:
        bool: True si se eliminó exitosamente de al menos un caché
    """
    django_success = False
    memory_success = False

    try:
        # Intentar eliminar del caché de Django
        django_cache.delete(key)
        django_success = True
        logger.debug(f"✅ Valor eliminado de caché Django: {key}")

    except Exception as e:
        logger.warning(f"⚠️ Error eliminando de caché Django: {e}")

    try:
        # Eliminar del caché en memoria
        if key in _memory_cache:
            _memory_cache.pop(key, None)
            _cache_timestamps.pop(key, None)
            memory_success = True
            logger.debug(f"✅ Valor eliminado de caché memoria: {key}")

    except Exception as mem_error:
        logger.error(f"❌ Error eliminando de caché memoria: {mem_error}")

    return django_success or memory_success


def clear_cache() -> bool:
    """
    Limpia completamente ambos cachés

    Returns:
        bool: True si se limpió exitosamente al menos un caché
    """
    django_success = False
    memory_success = False

    try:
        # Limpiar caché de Django
        django_cache.clear()
        django_success = True
        logger.info("✅ Caché Django limpiado")

    except Exception as e:
        logger.warning(f"⚠️ Error limpiando caché Django: {e}")

    try:
        # Limpiar caché en memoria
        _memory_cache.clear()
        _cache_timestamps.clear()
        memory_success = True
        logger.info("✅ Caché memoria limpiado")

    except Exception as mem_error:
        logger.error(f"❌ Error limpiando caché memoria: {mem_error}")

    return django_success or memory_success


def get_cache_info() -> dict:
    """
    Obtiene información sobre el estado de ambos cachés

    Returns:
        dict: Información sobre el estado de los cachés
    """
    info = {
        'django_cache_available': False,
        'memory_cache_size': len(_memory_cache),
        'memory_cache_keys': list(_memory_cache.keys()),
        'django_cache_error': None
    }

    try:
        # Probar caché de Django
        test_key = 'cache_test_key'
        django_cache.set(test_key, 'test', 1)
        django_cache.delete(test_key)
        info['django_cache_available'] = True

    except Exception as e:
        info['django_cache_error'] = str(e)

    return info
