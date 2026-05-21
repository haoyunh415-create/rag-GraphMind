import asyncio
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.ingestion.parser import DocumentParser
from app.ingestion.chunker import Chunker
from app.retrieval.vector_store import VectorStore
from app.retrieval.bm25_search import BM25Search
from app.retrieval.knowledge_graph import KnowledgeGraph
from app.retrieval.extractor import extract_from_chunks
from app.retrieval.health import retrieval_health
from app.core.config import get_settings


@dataclass
class IngestionResult:
    chunk_count: int
    index_statuses: dict[str, str]
    errors: list[str]


class IngestionPipeline:
    """End-to-end document ingestion: parse → chunk → index (3 stores) → extract entities."""

    def __init__(self):
        self.parser = DocumentParser()
        self.chunker = Chunker()
        self.vector_store = VectorStore()
        self.bm25 = BM25Search()
        self.graph = KnowledgeGraph()
        self.settings = get_settings()

    async def ingest(
        self,
        file_path: Path,
        document_id: str,
        document_name: str = "",
        parsed_text: str | None = None,
    ) -> IngestionResult:
        text = parsed_text if parsed_text is not None else await self.parser.parse(file_path)
        chunks = self.chunker.chunk(text, document_id)

        # Attach document name to each chunk for citation display
        for c in chunks:
            c["document_name"] = document_name or file_path.name

        if not chunks:
            return IngestionResult(
                chunk_count=0,
                index_statuses={"vector": "skipped", "bm25": "skipped", "graph": "skipped"},
                errors=["No chunks were produced from the document"],
            )

        health = await retrieval_health()
        index_jobs = {
            "vector": self.vector_store.insert(chunks),
        }
        skipped: dict[str, str] = {}
        if health["bm25"]["available"]:
            index_jobs["bm25"] = self.bm25.index(chunks)
        else:
            skipped["bm25"] = str(health["bm25"]["detail"])
        if health["graph"]["available"]:
            index_jobs["graph"] = self.graph.link_chunks(chunks)
        else:
            skipped["graph"] = str(health["graph"]["detail"])

        results = await asyncio.gather(
            *index_jobs.values(),
            return_exceptions=True,
        )

        index_statuses: dict[str, str] = {}
        errors: list[str] = []
        for name, result in zip(index_jobs.keys(), results):
            if isinstance(result, Exception):
                index_statuses[name] = "error"
                errors.append(f"{name}: {result}")
            elif result is False:
                index_statuses[name] = "error"
                errors.append(f"{name}: indexing failed")
            else:
                index_statuses[name] = "ready"

        for name, detail in skipped.items():
            index_statuses[name] = "skipped"
            errors.append(f"{name}: {detail}")

        # Entity extraction + graph insertion runs as a fire-and-forget task
        # (it's the slowest step — LLM calls per batch)
        if index_statuses.get("graph") == "ready":
            if not self.settings.graph_entity_extraction_enabled:
                index_statuses["graph_extract"] = "skipped"
            elif self.settings.graph_entity_extraction_sync:
                extraction_status, extraction_error = await self._extract_and_index_entities(chunks)
                index_statuses["graph_extract"] = extraction_status
                if extraction_error:
                    errors.append(f"graph_extract: {extraction_error}")
            else:
                index_statuses["graph_extract"] = "queued"
                asyncio.create_task(self._extract_and_index_entities(chunks))

        logger.info(
            f"Ingested '{document_name or file_path.name}': {len(chunks)} chunks | "
            f"indexes={index_statuses}"
        )
        return IngestionResult(
            chunk_count=len(chunks),
            index_statuses=index_statuses,
            errors=errors,
        )

    async def _extract_and_index_entities(self, chunks: list[dict]) -> tuple[str, str | None]:
        try:
            result = await asyncio.wait_for(
                extract_from_chunks(chunks),
                timeout=self.settings.graph_entity_extraction_timeout_seconds,
            )
            if result.entities:
                await self.graph.upsert_entities(result.entities, result.relations)
                return "ready", None
            return "empty", None
        except Exception as e:
            logger.error(f"Background entity extraction failed: {e}")
            return "error", str(e)
