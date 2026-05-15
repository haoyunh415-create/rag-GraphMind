from __future__ import annotations

import asyncio
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver
from loguru import logger

from app.core.config import get_settings
from app.retrieval.extractor import extract_query_entities


class KnowledgeGraph:
    """Neo4j-backed knowledge graph for entity-aware retrieval.

    Schema:
      (:Entity {id, name, type, properties})
      (:Chunk  {id, text, document_id})
      (:Entity)-[:MENTIONED_IN]->(:Chunk)
      (:Entity)-[:RELATES_TO {type, properties}]->(:Entity)
    """

    def __init__(self):
        self.settings = get_settings()
        self._driver: AsyncDriver | None = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Entity-aware graph search: extract entities from query → Cypher traversal → chunks."""
        entity_names = await extract_query_entities(query)
        if not entity_names:
            return []

        driver = self._get_driver()
        await self._ensure_schema()

        results: list[dict] = []

        async with driver.session() as session:
            # Strategy 1: Exact entity match → neighbours → chunks
            exact = await session.run(
                """
                MATCH (e:Entity)
                WHERE toLower(e.name) IN $names
                OPTIONAL MATCH (e)-[:RELATES_TO]->(related:Entity)
                OPTIONAL MATCH (e)-[:MENTIONED_IN]->(c:Chunk)
                OPTIONAL MATCH (related)-[:MENTIONED_IN]->(rc:Chunk)
                RETURN e.name AS entity, e.type AS entity_type,
                       collect(DISTINCT related.name) AS neighbours,
                       collect(DISTINCT {id: c.id, text: c.text, document_id: c.document_id}) AS chunks,
                       collect(DISTINCT {id: rc.id, text: rc.text, document_id: rc.document_id}) AS neighbour_chunks
                LIMIT $limit
                """,
                names=[n.lower().strip() for n in entity_names],
                limit=top_k,
            )
            async for record in exact:
                for chunk in (record.get("chunks") or []) + (record.get("neighbour_chunks") or []):
                    if chunk and chunk.get("id"):
                        results.append({
                            "id": chunk["id"],
                            "text": chunk["text"],
                            "document_id": chunk.get("document_id", ""),
                            "source": "graph",
                            "graph_context": {
                                "entity": record["entity"],
                                "entity_type": record["entity_type"],
                                "neighbours": record.get("neighbours", []),
                            },
                        })

            # Strategy 2: Fuzzy substring match on entity names
            if not results:
                for name in entity_names:
                    fuzzy = await session.run(
                        """
                        MATCH (e:Entity)
                        WHERE e.name CONTAINS $name OR $name CONTAINS e.name
                        MATCH (e)-[:MENTIONED_IN]->(c:Chunk)
                        RETURN e.name AS entity, e.type AS entity_type,
                               collect(DISTINCT {id: c.id, text: c.text, document_id: c.document_id}) AS chunks
                        LIMIT $limit
                        """,
                        name=name,
                        limit=max(1, top_k // len(entity_names)),
                    )
                    async for record in fuzzy:
                        for chunk in record.get("chunks") or []:
                            if chunk and chunk.get("id"):
                                results.append({
                                    "id": chunk["id"],
                                    "text": chunk["text"],
                                    "document_id": chunk.get("document_id", ""),
                                    "source": "graph",
                                    "graph_context": {
                                        "entity": record["entity"],
                                        "entity_type": record["entity_type"],
                                    },
                                })

        # Deduplicate by chunk id
        seen = set()
        unique: list[dict] = []
        for r in results:
            if r["id"] not in seen:
                seen.add(r["id"])
                unique.append(r)

        return unique[:top_k]

    async def upsert_entities(self, entities: list[dict], relations: list[dict]) -> None:
        """Insert or update entities and relations extracted from chunks."""
        if not entities:
            return

        driver = self._get_driver()
        await self._ensure_schema()

        async with driver.session() as session:
            # Upsert entities and link to chunks
            for e in entities:
                chunk_ids = e.get("source_chunk_ids", [])
                props = e.get("properties", {})
                await session.run(
                    """
                    MERGE (ent:Entity {name: $name})
                    SET ent.type = $type,
                        ent += $properties,
                        ent.id = coalesce(ent.id, randomUUID())
                    """,
                    name=e["name"],
                    type=e.get("type", "CONCEPT"),
                    properties=props,
                )
                # Link entity to source chunks
                for cid in chunk_ids:
                    await session.run(
                        """
                        MATCH (ent:Entity {name: $name})
                        MERGE (ch:Chunk {id: $chunk_id})
                        MERGE (ent)-[:MENTIONED_IN]->(ch)
                        """,
                        name=e["name"],
                        chunk_id=cid,
                    )

            # Upsert relations
            for r in relations:
                props = r.get("properties", {})
                await session.run(
                    """
                    MATCH (a:Entity {name: $source})
                    MATCH (b:Entity {name: $target})
                    MERGE (a)-[rel:RELATES_TO {type: $type}]->(b)
                    SET rel += $properties
                    """,
                    source=r["source"],
                    target=r["target"],
                    type=r.get("type", "RELATED_TO"),
                    properties=props,
                )

        logger.info(f"Upserted {len(entities)} entities and {len(relations)} relations")

    async def link_chunks(self, chunks: list[dict]) -> bool:
        """Create Chunk nodes (if they don't exist) so MENTIONED_IN relationships have targets."""
        driver = self._get_driver()
        await self._ensure_schema()

        async with driver.session() as session:
            for c in chunks:
                await session.run(
                    """
                    MERGE (ch:Chunk {id: $id})
                    SET ch.text = $text,
                        ch.document_id = $document_id,
                        ch.chunk_index = $chunk_index
                    """,
                    id=c["id"],
                    text=c.get("text", ""),
                    document_id=c.get("document_id", ""),
                    chunk_index=c.get("chunk_index", 0),
                )
        logger.info(f"Linked {len(chunks)} chunk nodes in graph")
        return True

    async def delete_document(self, document_id: str) -> bool:
        """Delete graph chunks and orphan entities for one document."""
        driver = self._get_driver()
        await self._ensure_schema()

        async with driver.session() as session:
            await session.run(
                """
                MATCH (c:Chunk {document_id: $document_id})
                DETACH DELETE c
                """,
                document_id=document_id,
            )
            await session.run(
                """
                MATCH (e:Entity)
                WHERE NOT (e)-[:MENTIONED_IN]->(:Chunk)
                DETACH DELETE e
                """
            )
        logger.info(f"Deleted graph chunks and orphan entities for document {document_id}")
        return True

    async def stats(self) -> dict:
        """Return entity/relation counts."""
        driver = self._get_driver()
        await self._ensure_schema()

        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (e:Entity)
                OPTIONAL MATCH ()-[r:RELATES_TO]->()
                OPTIONAL MATCH ()-[m:MENTIONED_IN]->()
                RETURN count(DISTINCT e) AS entities,
                       count(DISTINCT r) AS relations,
                       count(DISTINCT m) AS mentions
                """
            )
            record = await result.single()
            if record:
                return {
                    "entities": record["entities"],
                    "relations": record["relations"],
                    "mentions": record["mentions"],
                }
            return {"entities": 0, "relations": 0, "mentions": 0}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_driver(self) -> AsyncDriver:
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_user, self.settings.neo4j_password),
                max_connection_lifetime=3600,
                connection_acquisition_timeout=2,
                connection_timeout=3,
            )
        return self._driver

    async def _ensure_schema(self) -> None:
        """Create indexes and constraints if missing."""
        if self._initialized:
            return

        driver = self._get_driver()
        async with driver.session() as session:
            try:
                await session.run("CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE")
                await session.run("CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE")
                await session.run("CREATE INDEX chunk_id IF NOT EXISTS FOR (c:Chunk) ON (c.id)")
                await session.run("CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)")
            except Exception:
                pass  # constraints/indexes may already exist
        self._initialized = True
