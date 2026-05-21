from __future__ import annotations

import math
import re
from collections import defaultdict
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
    "一下",
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


def prune_citations(query: str, ranked_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the small evidence set used for answer generation and visible citations."""
    return prune_citations_with_report(query, ranked_results)["selected"]


def prune_citations_with_report(query: str, ranked_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Select citations and explain why each ranked candidate was kept or removed."""
    if not ranked_results:
        return {
            "selected": [],
            "candidates": [],
            "rejection_counts": {},
            "settings": _settings_snapshot(),
        }

    settings = get_settings()
    max_items = max(1, settings.citation_max_items)
    per_document_limit = max(1, settings.citation_per_document_limit)
    min_relative_score = _clamp01(settings.citation_min_relative_score)
    min_query_coverage = _clamp01(settings.citation_min_query_coverage)

    top_score = _top_score(ranked_results)
    scored = [
        _annotate_candidate(query=query, item=item, top_score=top_score)
        for item in ranked_results
        if isinstance(item, dict)
    ]
    if not scored:
        return {
            "selected": [],
            "candidates": [],
            "rejection_counts": {"invalid_candidate": len(ranked_results)},
            "settings": _settings_snapshot(),
        }

    selected: list[dict[str, Any]] = []
    per_document_counts: defaultdict[str, int] = defaultdict(int)

    for index, item in enumerate(scored):
        if len(selected) >= max_items:
            for remaining in scored[index:]:
                _mark_rejected(remaining, "max_items_limit")
            break
        document_key = str(item.get("document_id") or item.get("document_name") or item.get("id") or "")
        if per_document_counts[document_key] >= per_document_limit:
            _mark_rejected(item, "per_document_limit")
            continue
        passed, reason = _threshold_decision(
            item,
            min_relative_score=min_relative_score,
            min_query_coverage=min_query_coverage,
        )
        if not passed:
            _mark_rejected(item, reason)
            continue
        item["_citation_selected"] = True
        item["_citation_reason"] = reason
        selected.append(item)
        per_document_counts[document_key] += 1

    if not selected:
        first = scored[0]
        fallback_coverage = max(0.2, min_query_coverage * 0.7)
        if first["_citation_query_coverage"] >= fallback_coverage:
            first["_citation_selected"] = True
            first["_citation_reason"] = "fallback_minimum_evidence"
            first.pop("_citation_rejection_reason", None)
            selected = [first]

    return {
        "selected": selected,
        "candidates": scored,
        "rejection_counts": _rejection_counts(scored),
        "settings": _settings_snapshot(),
    }


def _annotate_candidate(query: str, item: dict[str, Any], top_score: float) -> dict[str, Any]:
    annotated = dict(item)
    score = _safe_float(annotated.get("score"))
    text = " ".join(
        str(part or "")
        for part in (
            annotated.get("document_name"),
            annotated.get("text"),
            annotated.get("highlight"),
            annotated.get("graph_context"),
        )
    )
    query_coverage = _query_coverage(query, text)
    relative_score = score / top_score if top_score > 0 else 1.0
    evidence_penalty = _insufficient_evidence_penalty(text)

    annotated["_citation_relative_score"] = round(_clamp01(relative_score), 4)
    annotated["_citation_query_coverage"] = round(query_coverage, 4)
    annotated["_citation_evidence_penalty"] = round(evidence_penalty, 4)
    annotated["_citation_selected"] = False
    return annotated


def _passes_thresholds(
    item: dict[str, Any],
    *,
    min_relative_score: float,
    min_query_coverage: float,
) -> bool:
    passed, _reason = _threshold_decision(
        item,
        min_relative_score=min_relative_score,
        min_query_coverage=min_query_coverage,
    )
    return passed


def _threshold_decision(
    item: dict[str, Any],
    *,
    min_relative_score: float,
    min_query_coverage: float,
) -> tuple[bool, str]:
    relative_score = _safe_float(item.get("_citation_relative_score"))
    query_coverage = _safe_float(item.get("_citation_query_coverage"))
    evidence_penalty = _safe_float(item.get("_citation_evidence_penalty"))
    sources = set(item.get("retrieval_sources") or [item.get("source", "")])

    source_bonus = 0.08 if len(sources) > 1 else 0.0
    if "graph" in sources:
        source_bonus += 0.05

    adjusted_coverage = query_coverage + source_bonus
    strong_score = relative_score >= min_relative_score
    strong_coverage = adjusted_coverage >= min_query_coverage
    direct_evidence = query_coverage >= min(0.6, min_query_coverage + 0.2)

    if evidence_penalty >= 0.25:
        return False, "insufficient_evidence"
    if not strong_coverage:
        return False, "query_coverage_low"
    if direct_evidence:
        return True, "direct_evidence"
    if strong_score:
        return True, "score_and_coverage"
    return False, "relative_score_low"


def _mark_rejected(item: dict[str, Any], reason: str) -> None:
    item["_citation_selected"] = False
    item["_citation_rejection_reason"] = reason


def _rejection_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for item in candidates:
        reason = item.get("_citation_rejection_reason")
        if reason:
            counts[str(reason)] += 1
    return dict(counts)


def _settings_snapshot() -> dict[str, Any]:
    settings = get_settings()
    return {
        "max_items": max(1, settings.citation_max_items),
        "per_document_limit": max(1, settings.citation_per_document_limit),
        "min_relative_score": _clamp01(settings.citation_min_relative_score),
        "min_query_coverage": _clamp01(settings.citation_min_query_coverage),
    }


def _query_coverage(query: str, text: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    text_tokens = _tokens(text)
    if not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    return _clamp01(len(overlap) / len(query_tokens))


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


def _insufficient_evidence_penalty(text: str) -> float:
    normalized = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not normalized:
        return 0.0
    hits = sum(1 for phrase in INSUFFICIENT_EVIDENCE_PHRASES if phrase in normalized)
    if not hits:
        return 0.0
    return min(0.45, 0.25 + 0.1 * (hits - 1))


def _top_score(items: list[dict[str, Any]]) -> float:
    return max((_safe_float(item.get("score")) for item in items), default=0.0)


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
