import logging
import os
import threading
import time

import redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
    socket_connect_timeout=0.5,
    socket_timeout=0.5,
    retry_on_timeout=False,
    retry=Retry(NoBackoff(), 0),
)

_fallback_cache: dict[str, tuple[str, float | None]] = {}
_fallback_lock = threading.Lock()
_use_fallback_cache = False
_fallback_warning_logged = False


def _log_fallback_once(exc: Exception) -> None:
    global _fallback_warning_logged
    if _fallback_warning_logged:
        return
    logger.warning("Redis unavailable (%s). Using in-memory cache fallback.", exc)
    _fallback_warning_logged = True


def _switch_to_fallback(exc: Exception) -> None:
    global _use_fallback_cache
    _use_fallback_cache = True
    _log_fallback_once(exc)


def _cleanup_expired_entries(now: float) -> None:
    expired_keys = [
        key for key, (_, expires_at) in _fallback_cache.items()
        if expires_at is not None and expires_at <= now
    ]
    for key in expired_keys:
        _fallback_cache.pop(key, None)


def get_cache(key):
    if _use_fallback_cache:
        with _fallback_lock:
            _cleanup_expired_entries(time.time())
            item = _fallback_cache.get(key)
            return item[0] if item else None

    try:
        return redis_client.get(key)
    except RedisError as exc:
        _switch_to_fallback(exc)
        with _fallback_lock:
            _cleanup_expired_entries(time.time())
            item = _fallback_cache.get(key)
            return item[0] if item else None


def set_cache(key, value, ttl=3600):
    if ttl <= 0:
        delete_cache(key)
        return

    if _use_fallback_cache:
        with _fallback_lock:
            _fallback_cache[key] = (value, time.time() + ttl)
        return

    try:
        redis_client.setex(key, ttl, value)
    except RedisError as exc:
        _switch_to_fallback(exc)
        with _fallback_lock:
            _fallback_cache[key] = (value, time.time() + ttl)


def delete_cache(key):
    if _use_fallback_cache:
        with _fallback_lock:
            _fallback_cache.pop(key, None)
        return

    try:
        redis_client.delete(key)
    except RedisError as exc:
        _switch_to_fallback(exc)
        with _fallback_lock:
            _fallback_cache.pop(key, None)


def delete_cache_by_prefix(prefix):
    if _use_fallback_cache:
        with _fallback_lock:
            keys_to_delete = [key for key in _fallback_cache if key.startswith(prefix)]
            for key in keys_to_delete:
                _fallback_cache.pop(key, None)
        return

    try:
        pattern = f"{prefix}*"
        keys = list(redis_client.scan_iter(match=pattern))
        if keys:
            redis_client.delete(*keys)
    except RedisError as exc:
        _switch_to_fallback(exc)
        with _fallback_lock:
            keys_to_delete = [key for key in _fallback_cache if key.startswith(prefix)]
            for key in keys_to_delete:
                _fallback_cache.pop(key, None)
