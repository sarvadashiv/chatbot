import redis
import os

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
    socket_connect_timeout=2
)

def get_cache(key):
    return redis_client.get(key)

def set_cache(key, value, ttl=3600):
    redis_client.setex(key, ttl, value)


def delete_cache(key):
    redis_client.delete(key)


def delete_cache_by_prefix(prefix):
    pattern = f"{prefix}*"
    keys = list(redis_client.scan_iter(match=pattern))
    if keys:
        redis_client.delete(*keys)
