from __future__ import annotations

import asyncio
import copy
import time
from urllib.parse import urlparse

from app.core.config import get_settings

_health_cache: dict[str, object] | None = None
_health_cache_lock = asyncio.Lock()


async def _can_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def clear_retrieval_health_cache() -> None:
    global _health_cache
    _health_cache = None


async def retrieval_health(force_refresh: bool = False) -> dict[str, dict[str, str | bool]]:
    global _health_cache
    settings = get_settings()
    ttl = max(0.0, settings.retrieval_health_cache_seconds)
    cache_key = f"{settings.es_host}|{settings.neo4j_uri}"
    now = time.monotonic()
    if not force_refresh and ttl > 0 and _health_cache:
        if _health_cache.get("key") == cache_key and now < float(_health_cache.get("expires_at", 0.0)):
            return copy.deepcopy(_health_cache["value"])

    async with _health_cache_lock:
        now = time.monotonic()
        if not force_refresh and ttl > 0 and _health_cache:
            if _health_cache.get("key") == cache_key and now < float(_health_cache.get("expires_at", 0.0)):
                return copy.deepcopy(_health_cache["value"])

        es = urlparse(settings.es_host)
        es_host = es.hostname or "localhost"
        es_port = es.port or (443 if es.scheme == "https" else 80)

        neo4j = urlparse(settings.neo4j_uri)
        neo4j_host = neo4j.hostname or "localhost"
        neo4j_port = neo4j.port or 7687

        bm25_ok, graph_ok = await asyncio.gather(
            _can_open(es_host, es_port),
            _can_open(neo4j_host, neo4j_port),
        )

        value = {
            "vector": {"available": True, "detail": "本地向量检索可用"},
            "bm25": {
                "available": bm25_ok,
                "detail": "Elasticsearch 可用" if bm25_ok else f"Elasticsearch 不可用：{settings.es_host}",
            },
            "graph": {
                "available": graph_ok,
                "detail": "Neo4j 可用" if graph_ok else f"Neo4j 不可用：{settings.neo4j_uri}",
            },
        }
        if ttl > 0:
            _health_cache = {
                "key": cache_key,
                "expires_at": now + ttl,
                "value": copy.deepcopy(value),
            }
        return value
