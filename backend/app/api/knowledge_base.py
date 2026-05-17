import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException, status
from loguru import logger

from app.core.config import get_settings
from app.models.schemas import (
    EvaluationRequest,
    EvaluationResult,
    KnowledgeBaseDocumentsResponse,
    KnowledgeBaseStats,
)
from app.retrieval.vector_store import VectorStore
from app.retrieval.bm25_search import BM25Search
from app.retrieval.knowledge_graph import KnowledgeGraph
from app.retrieval.health import retrieval_health

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])
EXTERNAL_STATS_TIMEOUT_SECONDS = 1.5


def _resolve_storage_path(path: str) -> Path:
    db_path = Path(path)
    if db_path.is_absolute():
        return db_path
    project_root = Path(__file__).resolve().parents[3]
    return project_root / db_path


def _storage_size_bytes(path: str) -> int:
    db_path = _resolve_storage_path(path)
    candidates = [
        db_path,
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
    ]
    total = 0
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                total += candidate.stat().st_size
        except OSError as exc:
            logger.warning(f"Could not read storage size for {candidate}: {exc}")
    return total


@router.get("/stats", response_model=KnowledgeBaseStats)
async def get_stats():
    settings = get_settings()
    vs = VectorStore()
    backend_health = await retrieval_health()

    es_task = (
        asyncio.wait_for(BM25Search().count(), timeout=EXTERNAL_STATS_TIMEOUT_SECONDS)
        if backend_health["bm25"]["available"]
        else _zero()
    )
    graph_task = (
        asyncio.wait_for(KnowledgeGraph().stats(), timeout=EXTERNAL_STATS_TIMEOUT_SECONDS)
        if backend_health["graph"]["available"]
        else _empty_graph_stats()
    )

    chunks, documents, es_count, graph_stats = await asyncio.gather(
        vs.count(),
        vs.list_documents(),
        es_task,
        graph_task,
        return_exceptions=True,
    )

    if isinstance(chunks, Exception):
        logger.warning(f"Vector store stats failed: {chunks}")
        chunks = 0
    if isinstance(documents, Exception):
        logger.warning(f"Vector document stats failed: {documents}")
        documents = []
    if isinstance(es_count, Exception):
        logger.warning(f"Elasticsearch stats failed: {es_count}")
        es_count = 0
    if isinstance(graph_stats, Exception):
        logger.warning(f"Graph stats failed: {graph_stats}")
        graph_stats = {"entities": 0, "relations": 0}

    return KnowledgeBaseStats(
        total_documents=len(documents),
        total_chunks=sum(doc.get("chunk_count", 0) for doc in documents),
        total_entities=graph_stats.get("entities", 0),
        total_relations=graph_stats.get("relations", 0),
        storage_size_bytes=_storage_size_bytes(settings.sqlite_db_path),
    )


async def _zero() -> int:
    return 0


async def _empty_graph_stats() -> dict[str, int]:
    return {"entities": 0, "relations": 0, "mentions": 0}


@router.get("/documents", response_model=KnowledgeBaseDocumentsResponse)
async def list_documents():
    vs = VectorStore()
    documents = await vs.list_documents()
    return KnowledgeBaseDocumentsResponse(documents=documents)


@router.post("/evaluate", response_model=EvaluationResult)
async def evaluate_rag(request: EvaluationRequest):
    """Evaluation is intentionally disabled until real scoring is wired in."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "RAG quality evaluation is not enabled in this stable build. "
            "The chat, upload, citation, and trace flow remain available."
        ),
    )
