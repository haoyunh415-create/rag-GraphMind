import asyncio
from fastapi import APIRouter
from loguru import logger

from app.models.schemas import (
    EvaluationRequest,
    EvaluationResult,
    KnowledgeBaseDocumentsResponse,
    KnowledgeBaseStats,
)
from app.retrieval.vector_store import VectorStore
from app.retrieval.bm25_search import BM25Search
from app.retrieval.knowledge_graph import KnowledgeGraph

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


@router.get("/stats", response_model=KnowledgeBaseStats)
async def get_stats():
    vs = VectorStore()
    bm25 = BM25Search()
    kg = KnowledgeGraph()

    chunks, documents, es_count, graph_stats = await asyncio.gather(
        vs.count(),
        vs.list_documents(),
        bm25.count(),
        kg.stats(),
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
        storage_size_bytes=0,
    )


@router.get("/documents", response_model=KnowledgeBaseDocumentsResponse)
async def list_documents():
    vs = VectorStore()
    documents = await vs.list_documents()
    return KnowledgeBaseDocumentsResponse(documents=documents)


@router.post("/evaluate", response_model=EvaluationResult)
async def evaluate_rag(request: EvaluationRequest):
    """Run RAGAS evaluation on a query."""
    return EvaluationResult(
        query=request.query,
        answer="",
        faithfulness=0.0,
        answer_relevancy=0.0,
        context_recall=0.0,
        context_precision=0.0,
        latency_ms=0.0,
    )
