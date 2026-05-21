"""
Entity and relation extraction from text via LLM structured output.
"""

import json
import asyncio
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.core.config import get_settings
from app.core.llm_client import get_llm_client

EXTRACTION_SYSTEM = """You are an entity and relation extraction engine. Extract all meaningful entities and their relationships from the text.

Entity types: PERSON, ORGANIZATION, PRODUCT, LOCATION, DATE, EVENT, METRIC, TECHNOLOGY, DOCUMENT, CONCEPT
Relation types: MANAGES, OWNS, WORKS_FOR, PART_OF, SUPPLIES, DEPENDS_ON, LOCATED_IN, OCCURRED_ON, MEASURES, REFERENCES, PRODUCES, COMPETES_WITH

Rules:
- Extract every named entity (people, companies, products, locations, dates, metrics).
- For each pair of entities that have a clear relationship in the text, extract the relation.
- "properties" should be a flat dict of string→string with relevant attributes.
- Be thorough but precise. Do not invent entities or relations not present in the text.

Output ONLY valid JSON:
{
  "entities": [
    {"name": "Acme Corp", "type": "ORGANIZATION", "properties": {"industry": "manufacturing"}}
  ],
  "relations": [
    {"source": "Zhang San", "target": "Acme Corp", "type": "WORKS_FOR", "properties": {"role": "CTO"}}
  ]
}"""

EXTRACTION_BATCH_SIZE = 5  # chunks per LLM call


@dataclass
class ExtractionResult:
    entities: list[dict[str, Any]]
    relations: list[dict[str, Any]]


async def extract_from_chunks(chunks: list[dict[str, Any]]) -> ExtractionResult:
    """Extract entities and relations from text chunks using LLM structured output."""
    settings = get_settings()
    if not _has_real_llm_key(settings.openai_api_key):
        result = _extract_from_chunks_locally(chunks)
        logger.info(
            f"Locally extracted {len(result.entities)} entities, "
            f"{len(result.relations)} relations from {len(chunks)} chunks"
        )
        return result

    client = get_llm_client()

    all_entities: list[dict] = []
    all_relations: list[dict] = []

    for i in range(0, len(chunks), EXTRACTION_BATCH_SIZE):
        batch = chunks[i : i + EXTRACTION_BATCH_SIZE]
        combined = "\n\n---\n\n".join(c["text"] for c in batch)

        try:
            resp = await client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM},
                    {"role": "user", "content": f"Extract entities and relations from:\n\n{combined[:8000]}"},
                ],
                temperature=0.1,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )

            raw = resp.choices[0].message.content or "{}"
            data = json.loads(raw)
            entities = data.get("entities", [])
            relations = data.get("relations", [])

            # Attach source chunk info
            chunk_ids = [c["id"] for c in batch]
            for e in entities:
                e.setdefault("properties", {})
                e["source_chunk_ids"] = chunk_ids
            for r in relations:
                r.setdefault("properties", {})
                r["source_chunk_ids"] = chunk_ids

            all_entities.extend(entities)
            all_relations.extend(relations)

            if len(chunks) > EXTRACTION_BATCH_SIZE:
                await asyncio.sleep(0.05)

        except Exception as e:
            logger.error(f"Entity extraction failed for batch {i}: {e}")
            fallback = _extract_from_chunks_locally(batch)
            all_entities.extend(fallback.entities)
            all_relations.extend(fallback.relations)

    # Deduplicate entities by name (case-insensitive)
    deduped = _dedup_entities(all_entities)
    deduped_relations = _dedup_relations(all_relations)
    logger.info(f"Extracted {len(deduped)} entities, {len(all_relations)} relations from {len(chunks)} chunks")

    return ExtractionResult(entities=deduped, relations=deduped_relations)


def _dedup_entities(entities: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for e in entities:
        key = e["name"].lower().strip()
        if key in seen:
            # Merge chunk sources
            existing_ids = set(seen[key].get("source_chunk_ids", []))
            new_ids = e.get("source_chunk_ids", [])
            seen[key]["source_chunk_ids"] = list(existing_ids | set(new_ids))
            # Merge properties
            seen[key].setdefault("properties", {})
            for k, v in e.get("properties", {}).items():
                if k not in seen[key]["properties"]:
                    seen[key]["properties"][k] = v
        else:
            seen[key] = e
    return list(seen.values())


def _dedup_relations(relations: list[dict]) -> list[dict]:
    seen: dict[tuple[str, str, str], dict] = {}
    for relation in relations:
        source = str(relation.get("source", "")).strip()
        target = str(relation.get("target", "")).strip()
        rel_type = str(relation.get("type", "RELATED_TO") or "RELATED_TO").strip().upper()
        if not source or not target:
            continue
        key = (source.lower(), target.lower(), rel_type)
        relation["source"] = source
        relation["target"] = target
        relation["type"] = rel_type
        relation.setdefault("properties", {})
        if key in seen:
            existing_ids = set(seen[key].get("source_chunk_ids", []))
            seen[key]["source_chunk_ids"] = list(existing_ids | set(relation.get("source_chunk_ids", [])))
            for k, v in relation.get("properties", {}).items():
                seen[key].setdefault("properties", {})
                seen[key]["properties"].setdefault(k, v)
        else:
            seen[key] = relation
    return list(seen.values())


def _has_real_llm_key(value: str | None) -> bool:
    if not value:
        return False
    normalized = value.strip().lower()
    return normalized not in {"", "replace-me", "changeme", "test", "dummy"}


def _extract_from_chunks_locally(chunks: list[dict[str, Any]]) -> ExtractionResult:
    """Deterministic relation extraction for local/dev Graph RAG tests.

    The LLM extractor remains the primary path when configured. This fallback
    gives uploads a real graph loop in offline demos and CI.
    """
    entities: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    for chunk in chunks:
        text = str(chunk.get("text", ""))
        chunk_id = str(chunk.get("id", "") or chunk.get("chunk_id", ""))
        extracted_entities, extracted_relations = _extract_local_chunk(text)
        for entity in extracted_entities:
            entity["source_chunk_ids"] = [chunk_id] if chunk_id else []
            entities.append(entity)
        for relation in extracted_relations:
            relation["source_chunk_ids"] = [chunk_id] if chunk_id else []
            relations.append(relation)
    return ExtractionResult(entities=_dedup_entities(entities), relations=_dedup_relations(relations))


def _extract_local_chunk(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entities: dict[str, dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []

    def add_entity(name: str, entity_type: str = "CONCEPT") -> None:
        normalized = _clean_entity_name(name)
        if not normalized:
            return
        entities.setdefault(
            normalized.lower(),
            {"name": normalized, "type": entity_type, "properties": {"extractor": "local_rule"}},
        )

    def add_relation(source: str, target: str, relation_type: str, description: str) -> None:
        clean_source = _clean_entity_name(source)
        clean_target = _clean_entity_name(target)
        if not clean_source or not clean_target:
            return
        add_entity(clean_source, _guess_entity_type(clean_source, relation_type, is_source=True))
        add_entity(clean_target, _guess_entity_type(clean_target, relation_type, is_source=False))
        relations.append({
            "source": clean_source,
            "target": clean_target,
            "type": relation_type,
            "properties": {"description": description, "extractor": "local_rule"},
        })

    patterns = [
        (r"\b(.+?)\s+is\s+owned\s+by\s+(.+?)(?:\.|;|\n|$)", "OWNED_BY", "is owned by"),
        (r"\b(.+?)\s+owns\s+(.+?)(?:\.|;|\n|$)", "OWNS", "owns"),
        (r"\b(.+?)\s+is\s+headquartered\s+in\s+(.+?)(?:\.|;|\n|$)", "HEADQUARTERED_IN", "is headquartered in"),
        (r"\b(.+?)\s+operates\s+(.+?)(?:\.|;|\n|$)", "OPERATES", "operates"),
        (r"\b(.+?)\s+is\s+operated\s+by\s+(.+?)(?:\.|;|\n|$)", "OPERATED_BY", "is operated by"),
        (r"\b(.+?)\s+depends\s+on\s+(.+?)(?:\.|;|\n|$)", "DEPENDS_ON", "depends on"),
        (r"\b(.+?)\s+is\s+managed\s+by\s+(.+?)(?:\.|;|\n|$)", "MANAGED_BY", "is managed by"),
        (r"\b(.+?)\s+manages\s+(.+?)(?:\.|;|\n|$)", "MANAGES", "manages"),
        (r"\b(.+?)\s+maintains\s+(.+?)(?:\.|;|\n|$)", "MAINTAINS", "maintains"),
        (r"\b(.+?)\s+is\s+maintained\s+by\s+(.+?)(?:\.|;|\n|$)", "MAINTAINED_BY", "is maintained by"),
        (r"\b(.+?)\s+affects\s+(.+?)(?:\.|;|\n|$)", "AFFECTS", "affects"),
        (r"\b(.+?)\s+is\s+assigned\s+to\s+(.+?)(?:\.|;|\n|$)", "ASSIGNED_TO", "is assigned to"),
    ]
    for pattern, relation_type, description in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            add_relation(match.group(1), match.group(2), relation_type, description)

    for match in re.finditer(r"\b[A-Z][A-Za-z0-9]*(?:[A-Z][A-Za-z0-9]+)+(?:\s+[A-Z][A-Za-z0-9]*(?:[A-Z][A-Za-z0-9]+)+){0,2}\b", text):
        add_entity(match.group(0), "CONCEPT")
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){1,3}\b", text):
        value = match.group(0)
        if value.lower() not in {"Graph RAG", "Return policy", "Invoice process"}:
            add_entity(value, "CONCEPT")

    return list(entities.values()), relations


def _clean_entity_name(value: str) -> str:
    cleaned = re.sub(r"^(the|a|an)\s+", "", str(value).strip(), flags=re.IGNORECASE)
    cleaned = cleaned.strip(" \t\r\n\"'`.,;:!?()[]{}")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120]


def _guess_entity_type(name: str, relation_type: str, is_source: bool) -> str:
    lowered = name.lower()
    if relation_type == "HEADQUARTERED_IN" and not is_source:
        return "LOCATION"
    if relation_type in {"MANAGED_BY", "ASSIGNED_TO"} and not is_source:
        return "PERSON"
    if relation_type == "MANAGES" and is_source:
        return "PERSON"
    if any(word in lowered for word in ("team", "group", "labs", "corp", "company", "service")):
        return "ORGANIZATION"
    if any(word in lowered for word in ("platform", "app", "database", "db", "gateway")):
        return "TECHNOLOGY"
    return "CONCEPT"


async def extract_query_entities(query: str) -> list[str]:
    """Extract entity names from a user query for graph lookup."""
    settings = get_settings()
    if not _has_real_llm_key(settings.openai_api_key):
        return _fallback_query_entities(query)

    client = get_llm_client()

    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "Extract named entities (people, organizations, products, locations, technologies, metrics) from the user query. Output ONLY a JSON array of entity name strings. Example: [\"Acme Corp\", \"Q3\", \"revenue\"]"},
                    {"role": "user", "content": query},
                ],
                temperature=0.0,
                max_tokens=200,
                response_format={"type": "json_object"},
            ),
            timeout=8.0,
        )
        raw = resp.choices[0].message.content or "[]"
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return list(data.values())[0] if data.values() else []
        return []
    except Exception as e:
        logger.warning(f"Query entity extraction failed: {e}")
        return _fallback_query_entities(query)


def _fallback_query_entities(query: str) -> list[str]:
    """Best-effort local entity extraction for offline graph tests."""
    stop_words = {
        "a",
        "about",
        "according",
        "and",
        "are",
        "does",
        "for",
        "from",
        "how",
        "is",
        "of",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
    }

    candidates: list[str] = []
    for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', query):
        quoted = match.group(1) or match.group(2)
        if quoted:
            candidates.append(quoted.strip())

    for match in re.finditer(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3}\b", query):
        value = match.group(0).strip()
        if value.lower() not in stop_words:
            candidates.append(value)

    for match in re.finditer(r"\b[A-Za-z]+[A-Z][A-Za-z0-9]*\b", query):
        value = match.group(0).strip()
        if value.lower() not in stop_words:
            candidates.append(value)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = " ".join(candidate.split()).strip(" ?.,;:!()[]{}")
        key = normalized.lower()
        if len(normalized) < 2 or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped[:8]
