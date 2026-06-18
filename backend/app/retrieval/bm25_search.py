from __future__ import annotations

from typing import Any

from elasticsearch import AsyncElasticsearch
from loguru import logger

from app.core.config import get_settings

INDEX_MAPPINGS = {
    "properties": {
        "id": {"type": "keyword"},
        "document_id": {"type": "keyword"},
        "document_name": {"type": "text"},
        "text": {"type": "text", "analyzer": "cjk"},
        "chunk_index": {"type": "integer"},
    }
}


class BM25Search:
    """Elasticsearch-backed BM25 full-text search."""

    def __init__(self):
        self.settings = get_settings()
        self._client: AsyncElasticsearch | None = None
        self._index_ready = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        """BM25 keyword search returning chunks with scores."""
        top_k = top_k or self.settings.top_k
        client = self._get_client()
        await self._ensure_index()

        try:
            resp = await client.search(
                index=self.settings.es_index,
                body={
                    "query": {
                        "multi_match": {
                            "query": query,
                            "fields": ["text^2", "document_name"],
                            "type": "best_fields",
                        }
                    },
                    "size": top_k,
                    "highlight": {
                        "fields": {"text": {"fragment_size": 200, "number_of_fragments": 1}}
                    },
                },
            )

            hits = resp["hits"]["hits"]
            return [
                {
                    "id": hit["_id"],
                    "document_id": hit["_source"].get("document_id", ""),
                    "document_name": hit["_source"].get("document_name", ""),
                    "text": hit["_source"].get("text", ""),
                    "chunk_index": hit["_source"].get("chunk_index", 0),
                    "score": float(hit["_score"] or 0.0),
                    "highlight": hit.get("highlight", {}).get("text", [""])[0],
                }
                for hit in hits
            ]
        except Exception as e:
            logger.error(f"Elasticsearch search failed: {e}")
            return []

    async def index(self, chunks: list[dict[str, Any]]) -> bool:
        """Bulk-index chunks into Elasticsearch."""
        if not chunks:
            return True

        client = self._get_client()
        try:
            await self._ensure_index()
        except Exception as e:
            logger.error(f"Elasticsearch index setup failed: {e}")
            return False

        body: list[Any] = []
        for c in chunks:
            body.append({"index": {"_index": self.settings.es_index, "_id": c["id"]}})
            body.append({
                "id": c["id"],
                "document_id": c.get("document_id", ""),
                "document_name": c.get("document_name", ""),
                "text": c.get("text", ""),
                "chunk_index": c.get("chunk_index", 0),
            })

        try:
            await client.bulk(body=body, refresh=True)
            logger.info(f"Indexed {len(chunks)} chunks into Elasticsearch")
            return True
        except Exception as e:
            logger.error(f"Elasticsearch bulk index failed: {e}")
            return False

    async def delete(self, document_id: str) -> bool:
        """Remove all chunks belonging to a document."""
        client = self._get_client()
        try:
            await self._ensure_index()
        except Exception as e:
            logger.error(f"Elasticsearch index setup failed: {e}")
            return False

        try:
            await client.delete_by_query(
                index=self.settings.es_index,
                body={"query": {"term": {"document_id": document_id}}},
                refresh=True,
            )
            logger.info(f"Deleted ES chunks for document {document_id}")
            return True
        except Exception as e:
            logger.error(f"Elasticsearch delete failed: {e}")
            return False

    async def count(self) -> int:
        """Return total indexed chunks."""
        client = self._get_client()
        await self._ensure_index()

        try:
            resp = await client.count(index=self.settings.es_index)
            return resp["count"]
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_client(self) -> AsyncElasticsearch:
        if self._client is None:
            self._client = AsyncElasticsearch(
                hosts=[self.settings.es_host],
                request_timeout=2,
                max_retries=0,
            )
        return self._client

    async def _ensure_index(self) -> None:
        if self._index_ready:
            return
        client = self._get_client()
        try:
            exists = await client.indices.exists(index=self.settings.es_index)
            if not exists:
                await client.indices.create(
                    index=self.settings.es_index,
                    body={
                        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
                        "mappings": INDEX_MAPPINGS,
                    },
                )
                logger.info(f"Created Elasticsearch index '{self.settings.es_index}'")
        except Exception as e:
            logger.warning(f"Elasticsearch unavailable at {self.settings.es_host}: {e}")
            raise ConnectionError(f"Elasticsearch connection failed: {e}")
        self._index_ready = True
