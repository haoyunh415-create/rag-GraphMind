from __future__ import annotations

from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase
from loguru import logger

from app.core.config import get_settings
from app.retrieval.extractor import extract_query_entities


class KnowledgeGraph:
    """Neo4j-backed knowledge graph for entity-aware retrieval.

    Schema:
      (:Entity {id, name, type, properties})
      (:Chunk  {id, text, document_id, document_name, chunk_index})
      (:Entity)-[:MENTIONED_IN]->(:Chunk)
      (:Entity)-[:RELATES_TO {type, properties}]-(:Entity)
    """

    def __init__(self):
        self.settings = get_settings()
        self._driver: AsyncDriver | None = None
        self._initialized = False

    async def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Extract query entities, follow graph relation paths, and return evidence chunks."""
        entity_names = await extract_query_entities(query)
        if not entity_names:
            return []

        driver = self._get_driver()
        await self._ensure_schema()

        results: list[dict] = []
        async with driver.session() as session:
            exact = await session.run(
                """
                MATCH (e:Entity)
                WHERE toLower(e.name) IN $names
                OPTIONAL MATCH path = (e)-[:RELATES_TO*0..2]-(related:Entity)
                WITH e, related, path
                OPTIONAL MATCH (related)-[:MENTIONED_IN]->(c:Chunk)
                RETURN e.name AS entity, e.type AS entity_type,
                       related.name AS matched_entity,
                       related.type AS matched_entity_type,
                       [node IN nodes(path) | node.name] AS path_entities,
                       [rel IN relationships(path) | rel.type] AS path_relations,
                       collect(DISTINCT {
                           id: c.id,
                           text: c.text,
                           document_id: c.document_id,
                           document_name: c.document_name,
                           chunk_index: c.chunk_index
                       }) AS chunks
                LIMIT $limit
                """,
                names=[n.lower().strip() for n in entity_names],
                limit=top_k,
            )
            async for record in exact:
                for chunk in record.get("chunks") or []:
                    if chunk and chunk.get("id"):
                        results.append({
                            "id": chunk["id"],
                            "text": chunk["text"],
                            "document_id": chunk.get("document_id", ""),
                            "document_name": chunk.get("document_name", ""),
                            "chunk_index": chunk.get("chunk_index", 0),
                            "source": "graph",
                            "graph_context": {
                                "entity": record["entity"],
                                "entity_type": record["entity_type"],
                                "matched_entity": record.get("matched_entity"),
                                "matched_entity_type": record.get("matched_entity_type"),
                                "path_entities": record.get("path_entities") or [],
                                "path_relations": record.get("path_relations") or [],
                            },
                        })

            if not results:
                for name in entity_names:
                    fuzzy = await session.run(
                        """
                        MATCH (e:Entity)
                        WHERE e.name CONTAINS $name OR $name CONTAINS e.name
                        MATCH (e)-[:MENTIONED_IN]->(c:Chunk)
                        RETURN e.name AS entity, e.type AS entity_type,
                               collect(DISTINCT {
                                   id: c.id,
                                   text: c.text,
                                   document_id: c.document_id,
                                   document_name: c.document_name,
                                   chunk_index: c.chunk_index
                               }) AS chunks
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
                                    "document_name": chunk.get("document_name", ""),
                                    "chunk_index": chunk.get("chunk_index", 0),
                                    "source": "graph",
                                    "graph_context": {
                                        "entity": record["entity"],
                                        "entity_type": record["entity_type"],
                                        "path_entities": [record["entity"]],
                                        "path_relations": [],
                                    },
                                })

        seen = set()
        unique: list[dict] = []
        for result in results:
            if result["id"] not in seen:
                seen.add(result["id"])
                unique.append(result)

        return unique[:top_k]

    async def upsert_entities(self, entities: list[dict], relations: list[dict]) -> None:
        """Insert or update entities and relations extracted from chunks."""
        if not entities:
            return

        driver = self._get_driver()
        await self._ensure_schema()

        async with driver.session() as session:
            for entity in entities:
                chunk_ids = entity.get("source_chunk_ids", [])
                props = entity.get("properties", {})
                await session.run(
                    """
                    MERGE (ent:Entity {name: $name})
                    SET ent.type = $type,
                        ent += $properties,
                        ent.id = coalesce(ent.id, randomUUID())
                    """,
                    name=entity["name"],
                    type=entity.get("type", "CONCEPT"),
                    properties=props,
                )
                for chunk_id in chunk_ids:
                    await session.run(
                        """
                        MATCH (ent:Entity {name: $name})
                        MERGE (ch:Chunk {id: $chunk_id})
                        MERGE (ent)-[:MENTIONED_IN]->(ch)
                        """,
                        name=entity["name"],
                        chunk_id=chunk_id,
                    )

            for relation in relations:
                props = relation.get("properties", {})
                await session.run(
                    """
                    MATCH (a:Entity {name: $source})
                    MATCH (b:Entity {name: $target})
                    MERGE (a)-[rel:RELATES_TO {type: $type}]->(b)
                    SET rel += $properties
                    """,
                    source=relation["source"],
                    target=relation["target"],
                    type=relation.get("type", "RELATED_TO"),
                    properties=props,
                )

        logger.info(f"Upserted {len(entities)} entities and {len(relations)} relations")

    async def link_chunks(self, chunks: list[dict]) -> bool:
        """Create Chunk nodes so MENTIONED_IN relationships have retrievable targets."""
        driver = self._get_driver()
        await self._ensure_schema()

        async with driver.session() as session:
            for chunk in chunks:
                await session.run(
                    """
                    MERGE (ch:Chunk {id: $id})
                    SET ch.text = $text,
                        ch.document_id = $document_id,
                        ch.document_name = $document_name,
                        ch.chunk_index = $chunk_index
                    """,
                    id=chunk["id"],
                    text=chunk.get("text", ""),
                    document_id=chunk.get("document_id", ""),
                    document_name=chunk.get("document_name", ""),
                    chunk_index=chunk.get("chunk_index", 0),
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
                pass
        self._initialized = True
