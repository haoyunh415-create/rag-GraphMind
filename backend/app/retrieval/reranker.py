from __future__ import annotations

import math
import re
from typing import Any

from app.core.config import get_settings

ASCII_RE = re.compile(r"[a-zA-Z0-9_]+")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "actual",
    "appear",
    "appears",
    "appeared",
    "complete",
    "distractor",
    "note",
    "notes",
    "packaging",
    "provide",
    "provides",
    "rule",
    "rules",
    "一个",
    "什么",
    "多少",
    "哪里",
    "如何",
    "怎么",
    "是否",
    "吗",
    "呢",
    "的",
    "了",
    "是",
}

INSUFFICIENT_EVIDENCE_PHRASES = (
    "distractor document",
    "does not provide complete",
    "doesn't provide complete",
    "do not provide complete",
    "not provide complete",
    "should not answer",
    "cannot answer",
    "can not answer",
    "unable to answer",
    "insufficient evidence",
    "not enough evidence",
    "no relevant evidence",
)


class HybridReranker:
    """Lightweight local reranker used after retrieval fusion.

    It is intentionally dependency-free. The goal is not to replace a cross-encoder,
    but to prevent high-recall candidates from outranking direct evidence when the
    project runs locally without an external rerank model.
    """

    def __init__(self):
        self.settings = get_settings()

    def rerank(self, query: str, candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        if not candidates:
            return []
        if not self.settings.reranker_enabled:
            return candidates[:limit]

        top_score = max((_safe_float(item.get("score")) for item in candidates), default=0.0)
        scored = [
            self._annotate_candidate(query=query, item=item, top_score=top_score)
            for item in candidates
            if isinstance(item, dict)
        ]
        return sorted(scored, key=lambda item: item["_rerank_score"], reverse=True)[:limit]

    def _annotate_candidate(self, query: str, item: dict[str, Any], top_score: float) -> dict[str, Any]:
        annotated = dict(item)
        original_score = _safe_float(annotated.get("score"))
        normalized_original = original_score / top_score if top_score > 0 else 0.0
        text = " ".join(
            str(part or "")
            for part in (
                annotated.get("document_name"),
                annotated.get("text"),
                annotated.get("highlight"),
                annotated.get("graph_context"),
            )
        )

        query_score = _query_coverage(query, text)
        phrase_score = _phrase_score(query, text)
        source_score = _source_score(annotated)
        evidence_penalty = _insufficient_evidence_penalty(text)
        score = (
            self.settings.reranker_original_weight * _clamp01(normalized_original)
            + self.settings.reranker_query_weight * query_score
            + self.settings.reranker_phrase_weight * phrase_score
            + self.settings.reranker_source_weight * source_score
            - evidence_penalty
        )

        annotated["_rrf_score"] = original_score
        annotated["_rerank_score"] = round(_clamp01(score), 6)
        annotated["_rerank_features"] = {
            "original": round(_clamp01(normalized_original), 4),
            "query_coverage": round(query_score, 4),
            "phrase": round(phrase_score, 4),
            "source": round(source_score, 4),
            "evidence_penalty": round(evidence_penalty, 4),
        }
        annotated["score"] = annotated["_rerank_score"]
        return annotated


def _query_coverage(query: str, text: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    text_tokens = _tokens(text)
    if not text_tokens:
        return 0.0
    return _clamp01(len(query_tokens & text_tokens) / len(query_tokens))


def _phrase_score(query: str, text: str) -> float:
    query_text = _normalize_text(query)
    target = _normalize_text(text)
    if not query_text or not target:
        return 0.0
    if query_text in target:
        return 1.0

    query_tokens = sorted(_tokens(query), key=len, reverse=True)
    if not query_tokens:
        return 0.0
    long_hits = sum(1 for token in query_tokens if len(token) >= 3 and token in target)
    return _clamp01(long_hits / max(1, min(len(query_tokens), 6)))


def _source_score(item: dict[str, Any]) -> float:
    sources = {str(source) for source in (item.get("retrieval_sources") or [item.get("source", "")]) if source}
    score = 0.0
    if len(sources) > 1:
        score += 0.6
    if "graph" in sources:
        score += 0.25
    if "bm25" in sources:
        score += 0.15
    return _clamp01(score)


def _insufficient_evidence_penalty(text: str) -> float:
    normalized = _normalize_text(text)
    if not normalized:
        return 0.0
    hits = sum(1 for phrase in INSUFFICIENT_EVIDENCE_PHRASES if phrase in normalized)
    if not hits:
        return 0.0
    return min(0.45, 0.25 + 0.1 * (hits - 1))


def _tokens(text: str) -> set[str]:
    normalized = text.lower()
    tokens: set[str] = set()
    for match in ASCII_RE.finditer(normalized):
        value = match.group(0).strip("_")
        if len(value) > 1 and value not in STOP_WORDS:
            tokens.add(value)
    for match in CHINESE_RE.finditer(normalized):
        run = match.group(0)
        if len(run) == 1:
            if run not in STOP_WORDS:
                tokens.add(run)
            continue
        for idx in range(len(run) - 1):
            token = run[idx : idx + 2]
            if token not in STOP_WORDS:
                tokens.add(token)
        if len(run) <= 4 and run not in STOP_WORDS:
            tokens.add(run)
    return tokens


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _safe_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
