from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from loguru import logger

from app.agents.tools import decompose_query, synthesize_answer
from app.core.config import get_settings
from app.retrieval.bm25_search import BM25Search
from app.retrieval.fusion import FusionRanker
from app.retrieval.health import retrieval_health
from app.retrieval.knowledge_graph import KnowledgeGraph
from app.retrieval.vector_store import VectorStore


@dataclass
class RagEvaluation:
    query: str
    answer: str
    faithfulness: float
    answer_relevancy: float
    context_recall: float
    context_precision: float
    latency_ms: float


async def evaluate_query(query: str, expected_answer: str | None = None) -> RagEvaluation:
    """Run a lightweight, dependency-free RAG evaluation for one query."""
    started = time.perf_counter()
    settings = get_settings()
    top_k = max(1, min(settings.rerank_top_k or settings.top_k, 10))

    sub_queries = await decompose_query(query)
    contexts = await _retrieve_contexts(sub_queries, top_k=top_k)

    answer = ""
    if contexts:
        chunks: list[str] = []
        async for token in synthesize_answer(query, contexts, sub_queries):
            chunks.append(token)
        answer = "".join(chunks).strip()
    else:
        answer = "知识库中没有检索到相关内容，无法进行有依据的回答。"

    context_texts = [str(ctx.get("text", "")) for ctx in contexts]
    context_blob = "\n".join(context_texts)

    latency_ms = (time.perf_counter() - started) * 1000
    return RagEvaluation(
        query=query,
        answer=answer,
        faithfulness=_faithfulness(answer, context_blob),
        answer_relevancy=_overlap_score(query, answer),
        context_recall=_context_recall(expected_answer, context_blob, query),
        context_precision=_context_precision(query, context_texts),
        latency_ms=latency_ms,
    )


async def _retrieve_contexts(sub_queries: list[str], top_k: int) -> list[dict]:
    vector_store = VectorStore()
    bm25 = BM25Search()
    graph = KnowledgeGraph()
    fusion = FusionRanker()

    try:
        health = await retrieval_health()
    except Exception as e:
        logger.warning(f"Retrieval health check failed during evaluation: {e}")
        health = {
            "vector": {"available": True, "detail": "local vector store"},
            "bm25": {"available": False, "detail": str(e)},
            "graph": {"available": False, "detail": str(e)},
        }

    all_results: list[dict] = []
    for sq in sub_queries:
        jobs = {"vector": vector_store.search(sq, top_k)}
        if health["bm25"]["available"]:
            jobs["bm25"] = bm25.search(sq, top_k)
        if health["graph"]["available"]:
            jobs["graph"] = graph.search(sq, top_k)

        results_by_source = dict(zip(jobs.keys(), await asyncio.gather(*jobs.values(), return_exceptions=True)))
        for source, results in results_by_source.items():
            if isinstance(results, Exception):
                logger.warning(f"Evaluation retrieval failed for {source}: {results}")
                continue
            for item in results if isinstance(results, list) else []:
                item["source"] = source
                all_results.append(item)

    return await fusion.rank(" ".join(sub_queries), all_results, limit=top_k)


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    latin = re.findall(r"[a-z0-9]+", text)
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    return latin + cjk


def _content_tokens(text: str) -> list[str]:
    stopwords = {
        "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are", "was", "were",
        "this", "that", "it", "with", "as", "by", "from", "be", "can", "your", "you",
        "的", "了", "和", "是", "在", "有", "与", "及", "或", "我", "你", "他", "她", "它",
        "这", "那", "请", "吗", "呢", "吧", "一个", "根据",
    }
    return [token for token in _tokenize(text) if token not in stopwords and len(token.strip()) > 0]


def _overlap_score(reference: str, candidate: str) -> float:
    ref = set(_content_tokens(reference))
    cand = set(_content_tokens(candidate))
    if not ref or not cand:
        return 0.0
    return _clamp(len(ref & cand) / len(ref))


def _faithfulness(answer: str, context: str) -> float:
    answer_tokens = set(_content_tokens(_strip_markdown_labels(answer)))
    context_tokens = set(_content_tokens(context))
    if not answer_tokens:
        return 0.0
    if not context_tokens:
        return 0.0
    return _clamp(len(answer_tokens & context_tokens) / len(answer_tokens))


def _context_precision(query: str, contexts: Iterable[str]) -> float:
    scores = [_overlap_score(query, text) for text in contexts if text.strip()]
    if not scores:
        return 0.0
    non_zero = [score for score in scores if score > 0]
    if not non_zero:
        return 0.0
    return _clamp(sum(non_zero) / len(scores))


def _context_recall(expected_answer: str | None, context: str, query: str) -> float:
    target = expected_answer.strip() if expected_answer else query
    target_tokens = set(_content_tokens(target))
    context_tokens = set(_content_tokens(context))
    if not target_tokens or not context_tokens:
        return 0.0
    return _clamp(len(target_tokens & context_tokens) / len(target_tokens))


def _strip_markdown_labels(text: str) -> str:
    text = re.sub(r"^#{1,6}\s+.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[*-]\s+", "", text, flags=re.MULTILINE)
    return text


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 4)))
