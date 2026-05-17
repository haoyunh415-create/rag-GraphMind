import asyncio
import hashlib
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, status as http_status
from loguru import logger

from app.core.config import get_settings
from app.models.schemas import DocumentChunksResponse, DocumentUploadResponse
from app.ingestion.pipeline import IngestionPipeline
from app.retrieval.vector_store import VectorStore

router = APIRouter(prefix="/api/documents", tags=["documents"])

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


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(file: UploadFile = File(...)):
    """Upload and ingest a document into the RAG pipeline."""
    settings = get_settings()
    doc_id = str(uuid.uuid4())
    filename = file.filename or "upload"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "Unsupported document type. Supported extensions: "
                + ", ".join(sorted(SUPPORTED_EXTENSIONS))
            ),
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Uploaded document is empty.",
        )
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Uploaded document exceeds the {settings.max_upload_bytes} byte limit.",
        )

    content_hash = hashlib.sha256(content).hexdigest()

    vector_store = VectorStore()
    existing = vector_store.find_document_by_hash(content_hash)
    if existing:
        return DocumentUploadResponse(
            document_id=existing["document_id"],
            filename=existing["document_name"] or filename,
            chunk_count=existing["chunk_count"] or 0,
            status="duplicate",
            index_statuses={"dedupe": "duplicate", **(existing.get("index_statuses") or {"vector": "ready"})},
            errors=existing.get("errors") or [],
        )

    # Save uploaded file to temp
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        pipeline = IngestionPipeline()
        parsed_text = await pipeline.parser.parse(tmp_path)
        candidate_chunks = pipeline.chunker.chunk(parsed_text, doc_id)
        existing = vector_store.find_document_by_chunk_texts([c["text"] for c in candidate_chunks])
        if existing:
            vector_store.register_document(
                existing["document_id"],
                existing["document_name"] or filename,
                content_hash,
                existing["chunk_count"] or len(candidate_chunks),
                {"vector": "ready", "bm25": "unknown", "graph": "unknown"},
                [],
            )
            return DocumentUploadResponse(
                document_id=existing["document_id"],
                filename=existing["document_name"] or filename,
                chunk_count=existing["chunk_count"] or len(candidate_chunks),
                status="duplicate",
                index_statuses={"dedupe": "duplicate", "vector": "ready"},
                errors=[],
            )

        result = await pipeline.ingest(tmp_path, doc_id, filename, parsed_text=parsed_text)
        chunk_count = result.chunk_count
        index_statuses = result.index_statuses
        errors = result.errors
        if chunk_count <= 0 or index_statuses.get("vector") != "ready":
            status = "error"
        elif any(value == "error" for value in index_statuses.values()):
            status = "partial"
        else:
            status = "ready"
        if status != "error":
            vector_store.register_document(doc_id, filename, content_hash, chunk_count, index_statuses, errors)
    except Exception:
        logger.exception(f"Document ingestion failed for '{filename}'")
        chunk_count = 0
        index_statuses = {}
        errors = ["Document ingestion failed"]
        status = "error"
    finally:
        tmp_path.unlink(missing_ok=True)

    return DocumentUploadResponse(
        document_id=doc_id,
        filename=filename,
        chunk_count=chunk_count,
        status=status,
        index_statuses=index_statuses,
        errors=errors,
    )


@router.get("/{document_id}/status")
async def get_document_status(document_id: str):
    from app.retrieval.vector_store import VectorStore
    vs = VectorStore()
    count = await vs.count()
    return {"document_id": document_id, "status": "ready", "total_chunks": count}


@router.get("/{document_id}/chunks", response_model=DocumentChunksResponse)
async def get_document_chunks(document_id: str):
    vs = VectorStore()
    chunks = await vs.list_document_chunks(document_id)
    return DocumentChunksResponse(document_id=document_id, chunks=chunks)


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
            errors.append(f"{name}: delete failed")
        else:
            statuses[name] = "deleted"

    for name, detail in skipped.items():
        statuses[name] = "skipped"
        logger.info(f"Skipping {name} delete for {document_id}: {detail}")

    status = "deleted" if not errors else "partial"
    return {"document_id": document_id, "status": status, "index_statuses": statuses, "errors": errors}
