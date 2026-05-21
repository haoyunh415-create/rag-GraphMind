import asyncio
import hashlib
import re
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile, File, HTTPException, status as http_status
from loguru import logger

from app.core.config import get_settings
from app.core.security import require_api_auth
from app.models.schemas import (
    DocumentChunksResponse,
    DocumentStatusUpdateRequest,
    DocumentStatusUpdateResponse,
    DocumentUploadResponse,
)
from app.ingestion.queue import IngestionJob, cancel_ingestion_job, enqueue_ingestion_job
from app.ingestion.queue import ensure_retryable_file, get_ingestion_queue_health, list_dead_letter_jobs
from app.retrieval.vector_store import VectorStore

router = APIRouter(prefix="/api/documents", tags=["文档"], dependencies=[Depends(require_api_auth)])

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".csv",
}

ALLOWED_CONTENT_TYPES = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".txt": {"text/plain"},
    ".md": {"text/markdown", "text/plain"},
    ".markdown": {"text/markdown", "text/plain"},
    ".html": {"text/html"},
    ".htm": {"text/html"},
    ".csv": {"text/csv", "application/csv", "text/plain"},
}
GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
UPLOAD_READ_SIZE = 1024 * 1024


def _safe_upload_filename(filename: str | None) -> str:
    name = Path((filename or "upload").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f]+", "", name)
    name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip(" .")
    return name[:180] or "upload"


def _validate_upload_type(filename: str, content_type: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported document type. Allowed extensions: "
            + ", ".join(sorted(SUPPORTED_EXTENSIONS)),
        )

    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if (
        normalized_content_type not in GENERIC_CONTENT_TYPES
        and normalized_content_type not in ALLOWED_CONTENT_TYPES[suffix]
    ):
        raise HTTPException(
            status_code=http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Upload MIME type '{normalized_content_type}' does not match "
                f"the '{suffix}' file extension"
            ),
        )
    return suffix


async def _save_upload_to_temp(
    file: UploadFile,
    suffix: str,
    max_bytes: int,
    upload_dir: Path,
) -> tuple[Path, str, int]:
    digest = hashlib.sha256()
    total = 0
    tmp_path: Path | None = None
    upload_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=upload_dir) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = await file.read(UPLOAD_READ_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Uploaded document exceeds the {max_bytes} byte limit",
                    )
                digest.update(chunk)
                tmp.write(chunk)
    except Exception:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise

    if total == 0:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Uploaded document is empty",
        )

    return tmp_path, digest.hexdigest(), total


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """上传文档，并写入 RAG 检索链路。"""
    settings = get_settings()
    doc_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    filename = _safe_upload_filename(file.filename)
    suffix = _validate_upload_type(filename, file.content_type)
    tmp_path, content_hash, _ = await _save_upload_to_temp(
        file,
        suffix,
        settings.max_upload_bytes,
        Path(settings.ingestion_queue_dir),
    )

    vector_store = VectorStore()
    try:
        existing = vector_store.find_document_by_hash(content_hash)
        if existing:
            tmp_path.unlink(missing_ok=True)
            return DocumentUploadResponse(
                document_id=existing["document_id"],
                filename=existing["document_name"] or filename,
                chunk_count=existing["chunk_count"] or 0,
                status="duplicate",
                job_id=existing.get("job_id") or None,
                index_statuses={"dedupe": "duplicate", **(existing.get("index_statuses") or {"vector": "ready"})},
                errors=existing.get("errors") or [],
                lifecycle_status=existing.get("lifecycle_status") or "enabled",
            )
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    index_statuses = {
        "ingestion": "queued",
        "vector": "queued",
        "bm25": "queued",
        "graph": "queued",
    }
    vector_store.register_document(
        doc_id,
        filename,
        content_hash,
        0,
        index_statuses,
        [],
        lifecycle_status="enabled",
        status="queued",
        job_id=job_id,
        attempt_count=0,
        max_attempts=settings.ingestion_max_attempts,
        last_error="",
        file_path=str(tmp_path),
    )
    try:
        await enqueue_ingestion_job(
            IngestionJob(
                file_path=str(tmp_path),
                document_id=doc_id,
                filename=filename,
                content_hash=content_hash,
                job_id=job_id,
            ),
            background_tasks,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return DocumentUploadResponse(
        document_id=doc_id,
        filename=filename,
        chunk_count=0,
        status="queued",
        job_id=job_id,
        index_statuses=index_statuses,
        errors=[],
        lifecycle_status="enabled",
    )


@router.get("/ingestion/health")
async def get_ingestion_health():
    return await get_ingestion_queue_health()


@router.get("/ingestion/dead-letter")
async def get_ingestion_dead_letter(limit: int = 20):
    limit = max(1, min(100, limit))
    return {"jobs": await list_dead_letter_jobs(limit=limit)}


@router.get("/{document_id}/status")
async def get_document_status(document_id: str):
    vs = VectorStore()
    documents = await vs.list_documents()
    for doc in documents:
        if doc.get("document_id") == document_id:
            return doc
    raise HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail="未找到对应文档",
    )


@router.get("/{document_id}/chunks", response_model=DocumentChunksResponse)
async def get_document_chunks(document_id: str):
    vs = VectorStore()
    chunks = await vs.list_document_chunks(document_id)
    return DocumentChunksResponse(document_id=document_id, chunks=chunks)


@router.patch("/{document_id}/status", response_model=DocumentStatusUpdateResponse)
async def update_document_status(document_id: str, request: DocumentStatusUpdateRequest):
    vs = VectorStore()
    updated = vs.update_document_lifecycle_status(document_id, request.lifecycle_status)
    if not updated:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="未找到对应文档，无法更新状态。",
        )
    is_retrievable = request.lifecycle_status == "enabled"
    documents = await vs.list_documents()
    for doc in documents:
        if doc.get("document_id") == document_id:
            is_retrievable = bool(doc.get("is_retrievable"))
            break
    return DocumentStatusUpdateResponse(
        document_id=document_id,
        lifecycle_status=request.lifecycle_status,
        is_retrievable=is_retrievable,
    )


@router.post("/{document_id}/cancel")
async def cancel_document_ingestion_endpoint(document_id: str):
    vs = VectorStore()
    documents = await vs.list_documents()
    document = next((doc for doc in documents if doc.get("document_id") == document_id), None)
    if not document:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="未找到对应文档",
        )
    if document.get("status") not in {"queued", "processing"}:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"文档当前状态为 {document.get('status')}，无法取消入库",
        )
    cancelled = await cancel_ingestion_job(document_id)
    if not cancelled:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="未找到对应文档",
        )
    return {
        "document_id": document_id,
        "status": "cancelled",
        "is_retrievable": False,
    }


@router.post("/{document_id}/retry")
async def retry_document_ingestion(document_id: str, background_tasks: BackgroundTasks):
    settings = get_settings()
    vs = VectorStore()
    record = vs.get_document_ingestion_record(document_id)
    if not record:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="未找到对应文档",
        )
    if record.get("status") not in {"error", "cancelled"}:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"文档当前状态为 {record.get('status')}，无法重试入库",
        )
    try:
        ensure_retryable_file(str(record.get("file_path") or ""))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    job_id = str(record.get("job_id") or "") or str(uuid.uuid4())
    vs.reset_document_ingestion_for_retry(
        document_id,
        job_id=job_id,
        max_attempts=settings.ingestion_max_attempts,
    )
    try:
        await enqueue_ingestion_job(
            IngestionJob(
                file_path=str(record["file_path"]),
                document_id=document_id,
                filename=str(record["document_name"]),
                content_hash=str(record["content_hash"]),
                job_id=job_id,
            ),
            background_tasks,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return {
        "document_id": document_id,
        "status": "queued",
        "job_id": job_id,
        "attempt_count": 0,
        "max_attempts": settings.ingestion_max_attempts,
    }


@router.delete("/{document_id}")
async def delete_document(document_id: str):
    from app.retrieval.vector_store import VectorStore
    from app.retrieval.bm25_search import BM25Search
    from app.retrieval.knowledge_graph import KnowledgeGraph
    from app.retrieval.health import retrieval_health

    health = await retrieval_health()
    stores = {
        "vector": VectorStore().delete(document_id),
    }
    skipped: dict[str, str] = {}
    if health["bm25"]["available"]:
        stores["bm25"] = BM25Search().delete(document_id)
    else:
        skipped["bm25"] = str(health["bm25"]["detail"])
    if health["graph"]["available"]:
        stores["graph"] = KnowledgeGraph().delete_document(document_id)
    else:
        skipped["graph"] = str(health["graph"]["detail"])

    results = await asyncio.gather(*stores.values(), return_exceptions=True)

    statuses: dict[str, str] = {}
    errors: list[str] = []
    for name, result in zip(stores.keys(), results):
        if isinstance(result, Exception):
            statuses[name] = "error"
            errors.append(f"{name}: {result}")
        elif result is False:
            statuses[name] = "error"
            errors.append(f"{name}: 删除失败")
        else:
            statuses[name] = "deleted"

    for name, detail in skipped.items():
        statuses[name] = "skipped"
        logger.info(f"Skipping {name} delete for {document_id}: {detail}")

    status = "deleted" if not errors else "partial"
    return {"document_id": document_id, "status": status, "index_statuses": statuses, "errors": errors}
