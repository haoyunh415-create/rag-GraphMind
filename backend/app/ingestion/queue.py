import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import redis.asyncio as aioredis
from fastapi import BackgroundTasks
from loguru import logger

from app.core.config import get_settings
from app.ingestion.jobs import cleanup_ingestion_file, run_ingestion_job
from app.retrieval.vector_store import VectorStore

QueueBackend = Literal["redis", "inline"]


@dataclass
class IngestionJob:
    file_path: str
    document_id: str
    filename: str
    content_hash: str
    job_id: str
    attempt: int = 1


def _queue_name() -> str:
    return get_settings().ingestion_queue_name


def _dlq_name() -> str:
    settings = get_settings()
    return settings.ingestion_dlq_name or f"{settings.ingestion_queue_name}:dead"


def _cancel_key() -> str:
    return f"{_queue_name()}:cancelled"


async def enqueue_ingestion_job(job: IngestionJob, background_tasks: BackgroundTasks) -> QueueBackend:
    settings = get_settings()
    if settings.ingestion_queue_mode != "inline":
        try:
            redis = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
            )
            await redis.rpush(_queue_name(), json.dumps(asdict(job), ensure_ascii=False))
            await redis.aclose()
            logger.info(f"Queued ingestion job {job.job_id} for document {job.document_id}")
            return "redis"
        except Exception as exc:
            if settings.ingestion_queue_mode == "redis":
                cleanup_ingestion_file(job.file_path)
                VectorStore().mark_document_ingestion_failed(
                    job.document_id,
                    ["Redis ingestion queue is unavailable"],
                )
                raise RuntimeError("Redis ingestion queue is unavailable") from exc
            logger.warning(f"Redis ingestion queue unavailable, falling back to inline task: {exc}")

    background_tasks.add_task(_run_inline_job, job)
    return "inline"


async def _run_inline_job(job: IngestionJob) -> None:
    try:
        await run_ingestion_job(
            job.file_path,
            job.document_id,
            job.filename,
            job.content_hash,
            job.job_id,
        )
    finally:
        cleanup_ingestion_file(job.file_path)


async def cancel_ingestion_job(document_id: str) -> bool:
    settings = get_settings()
    if settings.ingestion_queue_mode != "inline":
        try:
            redis = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
            )
            await redis.sadd(_cancel_key(), document_id)
            await redis.aclose()
        except Exception as exc:
            logger.info(f"Could not persist ingestion cancellation for {document_id}: {exc}")
    return VectorStore().cancel_document_ingestion(document_id)


async def get_ingestion_queue_health() -> dict:
    settings = get_settings()
    queue_name = _queue_name()
    dlq_name = _dlq_name()
    if settings.ingestion_queue_mode == "inline":
        return {
            "mode": "inline",
            "queue_name": queue_name,
            "dlq_name": dlq_name,
            "queue_length": 0,
            "dead_letter_length": 0,
            "redis_available": False,
            "detail": "inline mode",
        }

    try:
        redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        queue_length, dead_letter_length = await asyncio.gather(
            redis.llen(queue_name),
            redis.llen(dlq_name),
        )
        await redis.aclose()
        return {
            "mode": settings.ingestion_queue_mode,
            "queue_name": queue_name,
            "dlq_name": dlq_name,
            "queue_length": int(queue_length or 0),
            "dead_letter_length": int(dead_letter_length or 0),
            "redis_available": True,
            "detail": "ok",
        }
    except Exception as exc:
        return {
            "mode": settings.ingestion_queue_mode,
            "queue_name": queue_name,
            "dlq_name": dlq_name,
            "queue_length": 0,
            "dead_letter_length": 0,
            "redis_available": False,
            "detail": str(exc),
        }


async def list_dead_letter_jobs(limit: int = 20) -> list[dict]:
    settings = get_settings()
    if settings.ingestion_queue_mode == "inline":
        return []
    try:
        redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        rows = await redis.lrange(_dlq_name(), 0, max(0, limit - 1))
        await redis.aclose()
    except Exception as exc:
        logger.info(f"Could not read ingestion dead-letter queue: {exc}")
        return []

    jobs: list[dict] = []
    for raw in rows:
        try:
            jobs.append(json.loads(raw))
        except Exception:
            jobs.append({"raw": raw})
    return jobs


def ensure_retryable_file(file_path: str) -> None:
    if not file_path or not Path(file_path).exists():
        raise FileNotFoundError("Ingestion upload file is no longer available for retry")


async def is_ingestion_cancelled(redis: aioredis.Redis, document_id: str) -> bool:
    try:
        return bool(await redis.sismember(_cancel_key(), document_id))
    except Exception:
        return False


async def run_worker_forever() -> None:
    settings = get_settings()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    logger.info(f"Ingestion worker listening on Redis queue '{_queue_name()}'")
    while True:
        _, raw = await redis.blpop(_queue_name())
        await _handle_raw_job(redis, raw)


async def _handle_raw_job(redis: aioredis.Redis, raw: str) -> None:
    settings = get_settings()
    job = IngestionJob(**json.loads(raw))
    if await is_ingestion_cancelled(redis, job.document_id):
        VectorStore().cancel_document_ingestion(job.document_id)
        cleanup_ingestion_file(job.file_path)
        logger.info(f"Cancelled ingestion job {job.job_id} before processing")
        return

    vector_store = VectorStore()
    vector_store.mark_document_ingestion_processing(
        job.document_id,
        attempt_count=job.attempt,
        max_attempts=settings.ingestion_max_attempts,
        file_path=job.file_path,
    )
    status = await run_ingestion_job(
        job.file_path,
        job.document_id,
        job.filename,
        job.content_hash,
        job.job_id,
    )
    last_error = f"Document ingestion failed on attempt {job.attempt}/{settings.ingestion_max_attempts}"
    if status == "error" and job.attempt < settings.ingestion_max_attempts:
        next_job = IngestionJob(**{**asdict(job), "attempt": job.attempt + 1})
        vector_store.mark_document_ingestion_retrying(
            job.document_id,
            attempt_count=job.attempt,
            max_attempts=settings.ingestion_max_attempts,
            last_error=last_error,
        )
        logger.warning(
            f"Retrying ingestion job {job.job_id} for document {job.document_id} "
            f"({next_job.attempt}/{settings.ingestion_max_attempts})"
        )
        await asyncio.sleep(settings.ingestion_retry_delay_seconds)
        await redis.rpush(_queue_name(), json.dumps(asdict(next_job), ensure_ascii=False))
        return

    if status == "error":
        payload = {**asdict(job), "last_error": last_error}
        await redis.rpush(_dlq_name(), json.dumps(payload, ensure_ascii=False))
        vector_store.mark_document_ingestion_failed(
            job.document_id,
            [last_error],
            attempt_count=job.attempt,
            max_attempts=settings.ingestion_max_attempts,
            last_error=last_error,
        )
        logger.error(f"Moved ingestion job {job.job_id} for document {job.document_id} to DLQ")
        return

    cleanup_ingestion_file(job.file_path)
