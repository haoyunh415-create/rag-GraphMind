import asyncio
from fastapi import APIRouter
from loguru import logger

from app.models.schemas import (
    EvaluationHistoryResponse,
    EvaluationRecord,
    EvaluationRequest,
    EvaluationResult,
    KnowledgeBaseDocumentsResponse,
    KnowledgeBaseStats,
)
from app.retrieval.vector_store import VectorStore
from app.retrieval.bm25_search import BM25Search
from app.retrieval.knowledge_graph import KnowledgeGraph
from app.evaluation.rag import evaluate_query
from app.evaluation.store import list_evaluations, save_evaluation

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
    """Run a lightweight RAG evaluation on a query."""
    result = await evaluate_query(request.query, request.expected_answer)
    await save_evaluation(result, request.expected_answer)
    return EvaluationResult(
        query=result.query,
        answer=result.answer,
        faithfulness=result.faithfulness,
        answer_relevancy=result.answer_relevancy,
        context_recall=result.context_recall,
        context_precision=result.context_precision,
        latency_ms=result.latency_ms,
    )


@router.get("/evaluations", response_model=EvaluationHistoryResponse)
async def get_evaluations(limit: int = 50):
    """Return recent RAG evaluation results."""
    rows = await list_evaluations(limit=limit)
    return EvaluationHistoryResponse(
        evaluations=[EvaluationRecord(**row) for row in rows]
    )
