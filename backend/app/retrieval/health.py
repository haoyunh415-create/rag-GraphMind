from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from app.core.config import get_settings


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


async def retrieval_health() -> dict[str, dict[str, str | bool]]:
    settings = get_settings()

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

    return {
        "vector": {"available": True, "detail": "local vector store"},
        "bm25": {
            "available": bm25_ok,
            "detail": "Elasticsearch ready" if bm25_ok else f"Elasticsearch unavailable at {settings.es_host}",
        },
        "graph": {
            "available": graph_ok,
            "detail": "Neo4j ready" if graph_ok else f"Neo4j unavailable at {settings.neo4j_uri}",
        },
    }
