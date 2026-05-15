import hashlib
import asyncio
from typing import Any

import redis.asyncio as aioredis
from loguru import logger

from app.core.config import get_settings

_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis | None:
    global _client
    if _client is not None:
        return _client
    try:
        settings = get_settings()
        _client = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1)
        await _client.ping()
        logger.info("Connected to Redis")
    except Exception:
        logger.warning("Redis unavailable, caching disabled")
        _client = False  # type: ignore
        return None
    return _client


def _cache_key(query: str, contexts: list[dict]) -> str:
    """Produce a stable cache key from query + sorted context ids."""
    chunk_ids = sorted(c.get("id", "") for c in contexts)
    digest = hashlib.md5(f"{query}|{','.join(chunk_ids)}".encode()).hexdigest()
    return f"rag:answer:{digest}"


async def get_cached_answer(query: str, contexts: list[dict]) -> str | None:
    redis = await _get_redis()
    if not redis:
        return None
    key = _cache_key(query, contexts)
    try:
        cached = await asyncio.wait_for(redis.get(key), timeout=1.0)
        if cached:
            logger.info(f"Cache hit: {key}")
            return cached
    except Exception:
        pass
    return None


async def set_cached_answer(query: str, contexts: list[dict], answer: str, ttl: int = 3600) -> None:
    redis = await _get_redis()
    if not redis:
        return
    key = _cache_key(query, contexts)
    try:
        await asyncio.wait_for(redis.set(key, answer, ex=ttl), timeout=1.0)
        logger.info(f"Cached answer: {key}")
    except Exception:
        pass
