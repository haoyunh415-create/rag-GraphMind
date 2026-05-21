from pathlib import Path

from loguru import logger

from app.ingestion.pipeline import IngestionPipeline
from app.retrieval.vector_store import VectorStore


async def run_ingestion_job(
    file_path: str,
    doc_id: str,
    filename: str,
    content_hash: str,
    job_id: str,
) -> str:
    tmp_path = Path(file_path)
    vector_store = VectorStore()
    processing_statuses = {
        "ingestion": "processing",
        "vector": "queued",
        "bm25": "queued",
        "graph": "queued",
    }
    vector_store.register_document(
        doc_id,
        filename,
        content_hash,
        0,
        processing_statuses,
        [],
        lifecycle_status="enabled",
        status="processing",
        job_id=job_id,
    )
    try:
        pipeline = IngestionPipeline()
        result = await pipeline.ingest(tmp_path, doc_id, filename)
        chunk_count = result.chunk_count
        index_statuses = {"ingestion": "ready", **result.index_statuses}
        errors = result.errors
        if chunk_count <= 0 or index_statuses.get("vector") != "ready":
            status = "error"
        elif any(value == "error" for value in index_statuses.values()):
            status = "partial"
        else:
            status = "ready"
        if status == "error":
            index_statuses["ingestion"] = "error"
        vector_store.register_document(
            doc_id,
            filename,
            content_hash,
            chunk_count,
            index_statuses,
            errors,
            lifecycle_status="enabled",
            status=status,
            job_id=job_id,
        )
        return status
    except Exception:
        logger.exception(f"Document ingestion job failed for '{filename}'")
        vector_store.register_document(
            doc_id,
            filename,
            content_hash,
            0,
            {"ingestion": "error", "vector": "error", "bm25": "skipped", "graph": "skipped"},
            ["文档入库失败"],
            lifecycle_status="enabled",
            status="error",
            job_id=job_id,
        )
        return "error"


def cleanup_ingestion_file(file_path: str) -> None:
    Path(file_path).unlink(missing_ok=True)
