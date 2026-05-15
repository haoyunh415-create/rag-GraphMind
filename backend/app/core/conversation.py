import json
import asyncio
from typing import Any

import redis.asyncio as aioredis
from loguru import logger

from app.core.config import get_settings

_client: aioredis.Redis | None = None
MAX_HISTORY = 10  # max Q&A pairs to retain per conversation


async def _get_redis() -> aioredis.Redis | None:
    global _client
    if _client is not None:
        return _client
    try:
        settings = get_settings()
        _client = aioredis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1)
        await _client.ping()
    except Exception:
        _client = False  # type: ignore
        return None
    return _client


async def get_history(conversation_id: str) -> list[dict[str, str]]:
    """Retrieve the last N Q&A pairs for a conversation."""
    if not conversation_id:
        return []
    redis = await _get_redis()
    if not redis:
        return []
    try:
        raw = await asyncio.wait_for(redis.get(f"conv:{conversation_id}"), timeout=1.0)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return []


async def append_history(conversation_id: str, question: str, answer: str) -> None:
    """Append a Q&A pair and trim to MAX_HISTORY."""
    if not conversation_id:
        return
    redis = await _get_redis()
    if not redis:
        return
    try:
        history = await get_history(conversation_id)
        history.append({"question": question, "answer": answer})
        history = history[-MAX_HISTORY:]
        await asyncio.wait_for(
            redis.set(f"conv:{conversation_id}", json.dumps(history, ensure_ascii=False), ex=86400),
            timeout=1.0,
        )
    except Exception as e:
        logger.warning(f"Failed to save conversation history: {e}")
