"""
Entity and relation extraction from text via LLM structured output.
"""

import json
import asyncio
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
            continue

    # Deduplicate entities by name (case-insensitive)
    deduped = _dedup_entities(all_entities)
    logger.info(f"Extracted {len(deduped)} entities, {len(all_relations)} relations from {len(chunks)} chunks")

    return ExtractionResult(entities=deduped, relations=all_relations)


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


async def extract_query_entities(query: str) -> list[str]:
    """Extract entity names from a user query for graph lookup."""
    settings = get_settings()
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
        return []
