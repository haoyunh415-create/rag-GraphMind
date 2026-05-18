from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.models.schemas import EvaluationResult

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")

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
}


def evaluate_rag_answer(
    *,
    query: str,
    answer: str,
    contexts: list[dict[str, Any]],
    expected_answer: str | None = None,
    latency_ms: float = 0.0,
    query_id: str | None = None,
    conversation_id: str | None = None,
) -> EvaluationResult:
    """Score an answer against the actual retrieved context.

    The evaluator is deterministic and dependency-free. It is intentionally not
    a fake placeholder: every score is derived from the answer, query, retrieved
    chunks/citations, and optional expected answer.
    """

    clean_answer = answer.strip()
    normalized_contexts = _normalize_contexts(contexts)
    context_text = "\n\n".join(ctx["text"] for ctx in normalized_contexts)

    answer_tokens = _tokens(clean_answer)
    query_tokens = _tokens(query)
    context_tokens = _tokens(context_text)
    expected_tokens = _tokens(expected_answer or "")

    groundedness = _coverage(context_tokens, answer_tokens)
    answer_relevance = _f1(query_tokens, answer_tokens)
    if expected_tokens:
        answer_relevance = max(answer_relevance, _f1(expected_tokens, answer_tokens))

    statements = _answer_statements(clean_answer)
    supported_statements = _supported_statement_count(statements, normalized_contexts)
    citation_coverage = supported_statements / len(statements) if statements else 0.0

    retrieval_quality, context_precision, context_recall = _retrieval_scores(
        query_tokens=query_tokens,
        expected_tokens=expected_tokens,
        contexts=normalized_contexts,
        all_context_tokens=context_tokens,
    )

    if not clean_answer:
        groundedness = 0.0
        answer_relevance = 0.0
        citation_coverage = 0.0

    overall_score = _clamp01(
        groundedness * 0.35
        + answer_relevance * 0.25
        + citation_coverage * 0.2
        + retrieval_quality * 0.2
    )

    issues = _quality_issues(
        answer=clean_answer,
        contexts=normalized_contexts,
        groundedness=groundedness,
        answer_relevance=answer_relevance,
        citation_coverage=citation_coverage,
        retrieval_quality=retrieval_quality,
    )

    return EvaluationResult(
        evaluation_id=str(uuid4()),
        query_id=query_id,
        conversation_id=conversation_id,
        query=query,
        answer=clean_answer,
        overall_score=_round(overall_score),
        label=_label(overall_score),
        groundedness=_round(groundedness),
        answer_relevance=_round(answer_relevance),
        citation_coverage=_round(citation_coverage),
        retrieval_quality=_round(retrieval_quality),
        faithfulness=_round(groundedness),
        answer_relevancy=_round(answer_relevance),
        context_recall=_round(context_recall),
        context_precision=_round(context_precision),
        latency_ms=round(max(latency_ms, 0.0), 1),
        context_count=len(normalized_contexts),
        citation_count=len(normalized_contexts),
        issues=issues,
        details={
            "answer_token_count": len(answer_tokens),
            "query_token_count": len(query_tokens),
            "context_token_count": len(context_tokens),
            "statement_count": len(statements),
            "supported_statement_count": supported_statements,
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _normalize_contexts(contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for ctx in contexts:
        text = str(ctx.get("text") or "").strip()
        if not text:
            continue
        normalized.append({
            "source": str(ctx.get("source") or "unknown"),
            "document_id": str(ctx.get("document_id") or ""),
            "document_name": str(ctx.get("document_name") or ""),
            "chunk_id": str(ctx.get("chunk_id") or ctx.get("id") or ""),
            "chunk_index": ctx.get("chunk_index"),
            "text": text,
            "score": _safe_float(ctx.get("score")),
        })
    return normalized


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text.lower()):
        if raw in STOP_WORDS:
            continue
        if raw.isascii() and len(raw) <= 1 and not raw.isdigit():
            continue
        tokens.append(raw)
    return tokens


def _coverage(source_tokens: list[str], target_tokens: list[str]) -> float:
    source = set(source_tokens)
    target = set(target_tokens)
    if not source or not target:
        return 0.0
    return _clamp01(len(source & target) / len(target))


def _f1(left_tokens: list[str], right_tokens: list[str]) -> float:
    left = set(left_tokens)
    right = set(right_tokens)
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    if overlap == 0:
        return 0.0
    precision = overlap / len(right)
    recall = overlap / len(left)
    return _clamp01((2 * precision * recall) / (precision + recall))


def _answer_statements(answer: str) -> list[str]:
    statements: list[str] = []
    for raw_line in answer.splitlines():
        line = re.sub(r"^\s*[-*#>`\d.)\s]+", "", raw_line).strip()
        if not line:
            continue
        pieces = re.split(r"(?<=[.!?。！？])\s+", line)
        statements.extend(piece.strip() for piece in pieces if piece.strip())
    return statements


def _supported_statement_count(statements: list[str], contexts: list[dict[str, Any]]) -> int:
    if not contexts:
        return 0
    context_token_sets = [set(_tokens(ctx["text"])) for ctx in contexts]
    supported = 0
    for statement in statements:
        statement_tokens = set(_tokens(statement))
        if not statement_tokens:
            continue
        for context_tokens in context_token_sets:
            overlap = len(statement_tokens & context_tokens)
            coverage = overlap / len(statement_tokens)
            if coverage >= 0.45 or overlap >= min(4, len(statement_tokens)):
                supported += 1
                break
    return supported


def _retrieval_scores(
    *,
    query_tokens: list[str],
    expected_tokens: list[str],
    contexts: list[dict[str, Any]],
    all_context_tokens: list[str],
) -> tuple[float, float, float]:
    if not contexts:
        return 0.0, 0.0, 0.0

    relevance_scores: list[float] = []
    model_scores: list[float] = []
    for ctx in contexts:
        ctx_tokens = _tokens(ctx["text"])
        relevance_scores.append(_f1(query_tokens, ctx_tokens))
        model_scores.append(_normalize_retrieval_score(ctx.get("score", 0.0)))

    average_relevance = sum(relevance_scores) / len(relevance_scores)
    best_relevance = max(relevance_scores)
    average_model_score = sum(model_scores) / len(model_scores)
    retrieval_quality = _clamp01(best_relevance * 0.45 + average_relevance * 0.35 + average_model_score * 0.2)

    recall_target = expected_tokens or query_tokens
    context_recall = _coverage(all_context_tokens, recall_target)
    context_precision = average_relevance
    return retrieval_quality, context_precision, context_recall


def _normalize_retrieval_score(score: Any) -> float:
    value = _safe_float(score)
    if math.isnan(value) or math.isinf(value):
        return 0.0
    if 0.0 <= value <= 1.0:
        return value
    return _clamp01(value / (abs(value) + 1.0))


def _quality_issues(
    *,
    answer: str,
    contexts: list[dict[str, Any]],
    groundedness: float,
    answer_relevance: float,
    citation_coverage: float,
    retrieval_quality: float,
) -> list[str]:
    issues: list[str] = []
    if not answer:
        issues.append("empty_answer")
    if not contexts:
        issues.append("no_citations")
    if groundedness < 0.45:
        issues.append("low_groundedness")
    if answer_relevance < 0.35:
        issues.append("low_answer_relevance")
    if citation_coverage < 0.5:
        issues.append("low_citation_coverage")
    if retrieval_quality < 0.35:
        issues.append("low_retrieval_quality")
    return issues


def _label(score: float) -> str:
    if score >= 0.75:
        return "pass"
    if score >= 0.5:
        return "warn"
    return "fail"


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round(value: float) -> float:
    return round(_clamp01(value), 3)

