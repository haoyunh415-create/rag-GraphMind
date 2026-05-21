from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from app.core.config import get_settings
from app.core.llm_client import get_llm_client

BATCH_SIZE = 32
IN_MEMORY_INDEX: list[dict[str, Any]] = []
_index_lock = asyncio.Lock()
_embedding_model: Any = None  # lazy-loaded SentenceTransformer
_use_tfidf_fallback = False  # set to True when sentence-transformers can't load
_tfidf_vectorizer: Any = None
_tfidf_fitted = False
DOCUMENT_LIFECYCLE_STATUSES = {"enabled", "disabled", "test", "archived"}
DEFAULT_DOCUMENT_LIFECYCLE_STATUS = "enabled"
DOCUMENT_INGESTION_STATUSES = {"queued", "processing", "ready", "partial", "error", "duplicate", "cancelled"}
ACTIVE_DOCUMENT_STATUSES = {"queued", "processing"}


class VectorStore:
    """Hybrid vector store: Milvus → SQLite-vec → in-memory fallback."""

    def __init__(self):
        self.settings = get_settings()
        self._use_milvus = False
        self._collection: Any = None
        self._use_milvus = self._try_connect_milvus()
        self._use_sqlite = False
        self._sqlite_conn: Any = None
        self._sqlite_dim: int | None = None
        self._use_sqlite = self._try_init_sqlite()
        self._use_plain_sqlite = False
        if not self._use_sqlite:
            self._use_plain_sqlite = self._try_init_plain_sqlite()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        top_k = top_k or self.settings.top_k
        embedding = await self._embed([query])
        query_vec = embedding[0]

        if self._use_milvus:
            return await self._search_milvus(np.array(query_vec, dtype=np.float32), top_k)
        if self._use_sqlite:
            return await self._search_sqlite(query_vec, top_k)
        if self._use_plain_sqlite and _use_tfidf_fallback:
            return await self._search_memory(np.array(query_vec, dtype=np.float32), top_k)
        if self._use_plain_sqlite:
            return await self._search_plain_sqlite(query_vec, top_k)
        return await self._search_memory(np.array(query_vec, dtype=np.float32), top_k)

    async def insert(self, chunks: list[dict[str, Any]]) -> bool:
        if not chunks:
            return True

        texts = [c["text"] for c in chunks]
        embeddings = await self._embed(texts)

        # Always populate in-memory index (for TF-IDF fallback and count)
        async with _index_lock:
            for c, emb in zip(chunks, embeddings):
                IN_MEMORY_INDEX.append({
                    "id": c["id"],
                    "document_id": c.get("document_id", ""),
                    "document_name": c.get("document_name", ""),
                    "text": c.get("text", ""),
                    "chunk_index": c.get("chunk_index", 0),
                    "embedding": np.array(emb, dtype=np.float32),
                })
            total = len(IN_MEMORY_INDEX)

        # Persist to SQLite-vec
        if self._use_sqlite:
            try:
                await self._insert_sqlite(chunks, embeddings)
            except Exception as e:
                logger.warning(f"SQLite insert failed: {e}")

        # Try Milvus
        if self._use_milvus:
            try:
                await self._insert_milvus(chunks, embeddings)
            except Exception as e:
                logger.warning(f"Milvus insert failed (in-memory only): {e}")

        # Refit TF-IDF on new corpus
        if _use_tfidf_fallback and chunks:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _refit_tfidf)
            if self._use_plain_sqlite:
                await loop.run_in_executor(None, self._replace_plain_sqlite_from_memory)
        elif self._use_plain_sqlite:
            try:
                await self._insert_plain_sqlite(chunks, embeddings)
            except Exception as e:
                logger.warning(f"Plain SQLite insert failed: {e}")

        logger.info(f"Inserted {len(chunks)} chunks (total: {total})")
        return True

    async def delete(self, document_id: str) -> None:
        global IN_MEMORY_INDEX
        async with _index_lock:
            IN_MEMORY_INDEX = [c for c in IN_MEMORY_INDEX if c["document_id"] != document_id]
            total = len(IN_MEMORY_INDEX)

        if self._use_sqlite:
            try:
                await self._delete_sqlite(document_id)
            except Exception as e:
                logger.warning(f"SQLite delete failed: {e}")
        if self._use_plain_sqlite:
            try:
                await self._delete_plain_sqlite(document_id)
            except Exception as e:
                logger.warning(f"Plain SQLite delete failed: {e}")

        logger.info(f"Deleted chunks for document {document_id} (remaining: {total})")

    async def count(self) -> int:
        if self._use_sqlite:
            try:
                return await self._count_sqlite()
            except Exception:
                pass
        if self._use_plain_sqlite:
            try:
                return await self._count_plain_sqlite()
            except Exception:
                pass
        async with _index_lock:
            return len(IN_MEMORY_INDEX)

    async def list_documents(self) -> list[dict[str, Any]]:
        if self._use_sqlite:
            try:
                return await self._list_sqlite_documents()
            except Exception as e:
                logger.warning(f"SQLite document listing failed: {e}")
        if self._use_plain_sqlite:
            try:
                return await self._list_plain_sqlite_documents()
            except Exception as e:
                logger.warning(f"Plain SQLite document listing failed: {e}")

        async with _index_lock:
            rows = [
                (
                    c.get("document_id", ""),
                    c.get("document_name", ""),
                    c.get("chunk_index", 0),
                    c.get("text", ""),
                )
                for c in IN_MEMORY_INDEX
            ]
        return _documents_from_chunk_rows(rows)

    async def list_document_chunks(self, document_id: str) -> list[dict[str, Any]]:
        if self._use_sqlite:
            try:
                return await self._list_sqlite_document_chunks(document_id)
            except Exception as e:
                logger.warning(f"SQLite chunk listing failed: {e}")
        if self._use_plain_sqlite:
            try:
                return await self._list_plain_sqlite_document_chunks(document_id)
            except Exception as e:
                logger.warning(f"Plain SQLite chunk listing failed: {e}")

        async with _index_lock:
            chunks = [
                {
                    "chunk_id": c.get("id", ""),
                    "document_id": c.get("document_id", ""),
                    "document_name": c.get("document_name", ""),
                    "chunk_index": c.get("chunk_index", 0),
                    "text": c.get("text", ""),
                }
                for c in IN_MEMORY_INDEX
                if c.get("document_id") == document_id
            ]
        return sorted(chunks, key=lambda item: item["chunk_index"])

    def find_document_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        if not self._sqlite_conn:
            return None
        try:
            row = self._sqlite_conn.execute(
                """
                SELECT
                    document_id, document_name, chunk_count, index_statuses, errors,
                    lifecycle_status, status, job_id, attempt_count, max_attempts,
                    last_error, updated_at
                FROM rag_documents
                WHERE content_hash = ?
                """,
                (content_hash,),
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        return {
            "document_id": row[0],
            "document_name": row[1],
            "chunk_count": row[2],
            "content_hash": content_hash,
            "index_statuses": _json_dict(row[3]) if len(row) > 3 else {},
            "errors": _json_list(row[4]) if len(row) > 4 else [],
            "lifecycle_status": _normalize_lifecycle_status(row[5] if len(row) > 5 else None),
            "status": _normalize_ingestion_status(row[6] if len(row) > 6 else None, _json_dict(row[3]) if len(row) > 3 else {}),
            "job_id": str(row[7] or "") if len(row) > 7 else "",
            "attempt_count": int(row[8] or 0) if len(row) > 8 else 0,
            "max_attempts": int(row[9] or 0) if len(row) > 9 else 0,
            "last_error": str(row[10] or "") if len(row) > 10 else "",
            "updated_at": str(row[11] or "") if len(row) > 11 else "",
        }

    def find_document_by_chunk_texts(self, chunk_texts: list[str]) -> dict[str, Any] | None:
        target = _chunk_text_signature(chunk_texts)
        if not target or not self._sqlite_conn:
            return None

        try:
            if self._use_plain_sqlite:
                rows = self._sqlite_conn.execute(
                    """
                    SELECT document_id, document_name, chunk_index, text
                    FROM rag_chunks
                    ORDER BY document_id, chunk_index
                    """
                ).fetchall()
            elif self._use_sqlite:
                collection = self.settings.milvus_collection
                rows = self._sqlite_conn.execute(
                    f"""
                    SELECT document_id, document_name, chunk_index, text
                    FROM [{collection}]
                    ORDER BY document_id, chunk_index
                    """
                ).fetchall()
            else:
                return None
        except Exception:
            return None

        grouped: dict[str, dict[str, Any]] = {}
        for document_id, document_name, chunk_index, text in rows:
            if not document_id:
                continue
            item = grouped.setdefault(
                document_id,
                {"document_id": document_id, "document_name": document_name or "", "chunks": []},
            )
            item["chunks"].append((chunk_index or 0, text or ""))

        for item in grouped.values():
            ordered_texts = [text for _, text in sorted(item["chunks"], key=lambda pair: pair[0])]
            if _chunk_text_signature(ordered_texts) == target:
                return {
                    "document_id": item["document_id"],
                    "document_name": item["document_name"],
                    "chunk_count": len(ordered_texts),
                }
        return None

    def register_document(
        self,
        document_id: str,
        document_name: str,
        content_hash: str,
        chunk_count: int,
        index_statuses: dict[str, str] | None = None,
        errors: list[str] | None = None,
        lifecycle_status: str | None = None,
        status: str | None = None,
        job_id: str | None = None,
        attempt_count: int | None = None,
        max_attempts: int | None = None,
        last_error: str | None = None,
        file_path: str | None = None,
    ) -> None:
        if not self._sqlite_conn:
            return
        try:
            self._ensure_document_registry_table()
            task_meta = self._get_document_task_metadata(document_id)
            lifecycle_status = _normalize_lifecycle_status(
                lifecycle_status or self._get_document_lifecycle_status(document_id)
            )
            self._sqlite_conn.execute(
                """
                INSERT OR REPLACE INTO rag_documents
                    (
                        content_hash, document_id, document_name, chunk_count,
                        index_statuses, errors, lifecycle_status, status, job_id,
                        attempt_count, max_attempts, last_error, file_path, updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content_hash,
                    document_id,
                    document_name,
                    chunk_count,
                    json.dumps(index_statuses or {"vector": "ready", "bm25": "unknown", "graph": "unknown"}),
                    json.dumps(errors or []),
                    lifecycle_status,
                    _normalize_ingestion_status(status, index_statuses or {}),
                    job_id or self._get_document_job_id(document_id) or "",
                    int(attempt_count if attempt_count is not None else task_meta.get("attempt_count", 0)),
                    int(max_attempts if max_attempts is not None else task_meta.get("max_attempts", 0)),
                    str(last_error if last_error is not None else task_meta.get("last_error", "")),
                    str(file_path if file_path is not None else task_meta.get("file_path", "")),
                    _utc_now_iso(),
                ),
            )
            self._sqlite_conn.commit()
        except Exception as e:
            logger.warning(f"Document dedupe registry update failed: {e}")

    def update_document_lifecycle_status(self, document_id: str, lifecycle_status: str) -> bool:
        if not self._sqlite_conn:
            return False
        lifecycle_status = _normalize_lifecycle_status(lifecycle_status)
        try:
            self._ensure_document_registry_table()
            cursor = self._sqlite_conn.execute(
                """
                UPDATE rag_documents
                SET lifecycle_status = ?
                WHERE document_id = ?
                """,
                (lifecycle_status, document_id),
            )
            self._sqlite_conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.warning(f"Document lifecycle status update failed: {e}")
            return False

    def cancel_document_ingestion(self, document_id: str) -> bool:
        return self._update_document_ingestion_state(
            document_id,
            status="cancelled",
            index_statuses={"ingestion": "cancelled", "vector": "skipped", "bm25": "skipped", "graph": "skipped"},
            errors=[],
            last_error="",
        )

    def mark_document_ingestion_processing(
        self,
        document_id: str,
        attempt_count: int,
        max_attempts: int,
        file_path: str,
    ) -> bool:
        return self._update_document_ingestion_state(
            document_id,
            status="processing",
            index_statuses={"ingestion": "processing", "vector": "queued", "bm25": "queued", "graph": "queued"},
            errors=[],
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            file_path=file_path,
            last_error="",
        )

    def mark_document_ingestion_retrying(
        self,
        document_id: str,
        attempt_count: int,
        max_attempts: int,
        last_error: str,
    ) -> bool:
        return self._update_document_ingestion_state(
            document_id,
            status="queued",
            index_statuses={"ingestion": "queued", "vector": "queued", "bm25": "queued", "graph": "queued"},
            errors=[last_error] if last_error else [],
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            last_error=last_error,
        )

    def mark_document_ingestion_failed(
        self,
        document_id: str,
        errors: list[str],
        attempt_count: int | None = None,
        max_attempts: int | None = None,
        last_error: str | None = None,
    ) -> bool:
        return self._update_document_ingestion_state(
            document_id,
            status="error",
            index_statuses={"ingestion": "error", "vector": "error", "bm25": "skipped", "graph": "skipped"},
            errors=errors,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
            last_error=last_error if last_error is not None else (errors[-1] if errors else ""),
        )

    def reset_document_ingestion_for_retry(
        self,
        document_id: str,
        job_id: str,
        max_attempts: int,
    ) -> bool:
        return self._update_document_ingestion_state(
            document_id,
            status="queued",
            index_statuses={"ingestion": "queued", "vector": "queued", "bm25": "queued", "graph": "queued"},
            errors=[],
            job_id=job_id,
            attempt_count=0,
            max_attempts=max_attempts,
            last_error="",
        )

    def _update_document_ingestion_state(
        self,
        document_id: str,
        status: str,
        index_statuses: dict[str, str],
        errors: list[str],
        job_id: str | None = None,
        attempt_count: int | None = None,
        max_attempts: int | None = None,
        last_error: str | None = None,
        file_path: str | None = None,
    ) -> bool:
        if not self._sqlite_conn:
            return False
        try:
            self._ensure_document_registry_table()
            fields = [
                "status = ?",
                "index_statuses = ?",
                "errors = ?",
                "updated_at = ?",
            ]
            values: list[Any] = [
                _normalize_ingestion_status(status, index_statuses),
                json.dumps(index_statuses),
                json.dumps(errors),
                _utc_now_iso(),
            ]
            if job_id is not None:
                fields.append("job_id = ?")
                values.append(job_id)
            if attempt_count is not None:
                fields.append("attempt_count = ?")
                values.append(int(attempt_count))
            if max_attempts is not None:
                fields.append("max_attempts = ?")
                values.append(int(max_attempts))
            if last_error is not None:
                fields.append("last_error = ?")
                values.append(last_error)
            if file_path is not None:
                fields.append("file_path = ?")
                values.append(file_path)
            values.append(document_id)
            cursor = self._sqlite_conn.execute(
                f"UPDATE rag_documents SET {', '.join(fields)} WHERE document_id = ?",
                values,
            )
            self._sqlite_conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.warning(f"Document ingestion state update failed: {e}")
            return False

    def get_document_ingestion_record(self, document_id: str) -> dict[str, Any] | None:
        if not self._sqlite_conn:
            return None
        try:
            self._ensure_document_registry_table()
            row = self._sqlite_conn.execute(
                """
                SELECT
                    document_id, document_name, content_hash, chunk_count,
                    index_statuses, errors, lifecycle_status, status, job_id,
                    attempt_count, max_attempts, last_error, file_path, updated_at
                FROM rag_documents
                WHERE document_id = ?
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        return {
            "document_id": row[0],
            "document_name": row[1],
            "content_hash": row[2],
            "chunk_count": int(row[3] or 0),
            "index_statuses": _json_dict(row[4]),
            "errors": _json_list(row[5]),
            "lifecycle_status": _normalize_lifecycle_status(row[6]),
            "status": _normalize_ingestion_status(row[7], _json_dict(row[4])),
            "job_id": str(row[8] or ""),
            "attempt_count": int(row[9] or 0),
            "max_attempts": int(row[10] or 0),
            "last_error": str(row[11] or ""),
            "file_path": str(row[12] or ""),
            "updated_at": str(row[13] or ""),
        }

    def _get_document_lifecycle_status(self, document_id: str) -> str | None:
        if not self._sqlite_conn:
            return None
        try:
            self._ensure_document_registry_table()
            row = self._sqlite_conn.execute(
                """
                SELECT lifecycle_status
                FROM rag_documents
                WHERE document_id = ?
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        return _normalize_lifecycle_status(row[0])

    def _get_document_job_id(self, document_id: str) -> str | None:
        if not self._sqlite_conn:
            return None
        try:
            self._ensure_document_registry_table()
            row = self._sqlite_conn.execute(
                """
                SELECT job_id
                FROM rag_documents
                WHERE document_id = ?
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        except Exception:
            return None
        if not row:
            return None
        return str(row[0] or "") or None

    def _get_document_task_metadata(self, document_id: str) -> dict[str, Any]:
        if not self._sqlite_conn:
            return {}
        try:
            self._ensure_document_registry_table()
            row = self._sqlite_conn.execute(
                """
                SELECT attempt_count, max_attempts, last_error, file_path, updated_at
                FROM rag_documents
                WHERE document_id = ?
                LIMIT 1
                """,
                (document_id,),
            ).fetchone()
        except Exception:
            return {}
        if not row:
            return {}
        return {
            "attempt_count": int(row[0] or 0),
            "max_attempts": int(row[1] or 0),
            "last_error": str(row[2] or ""),
            "file_path": str(row[3] or ""),
            "updated_at": str(row[4] or ""),
        }

    async def list_retrievable_document_ids(self) -> set[str]:
        documents = await self.list_documents()
        return {
            str(doc.get("document_id") or "")
            for doc in documents
            if doc.get("document_id") and doc.get("is_retrievable")
        }

    # ------------------------------------------------------------------
    # SQLite-vec (local persistent vector store)
    # ------------------------------------------------------------------

    def _try_init_sqlite(self) -> bool:
        """Initialize SQLite with the sqlite-vec extension loaded."""
        try:
            import sqlite_vec
            import sqlite3

            db_path = _resolve_sqlite_path(self.settings.sqlite_db_path)
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            self._sqlite_conn = conn
            self._sqlite_dim = self._read_existing_dim()
            self._ensure_document_registry_table()
            logger.info(f"SQLite-vec initialized at {db_path}" +
                        (f" (dim={self._sqlite_dim})" if self._sqlite_dim else ""))
            return True
        except Exception as e:
            logger.warning(f"sqlite-vec unavailable ({e}), using in-memory fallback")
            return False

    def _read_existing_dim(self) -> int | None:
        """Read dimension from existing vec0 table if present."""
        try:
            collection = self.settings.milvus_collection
            row = self._sqlite_conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (collection,)
            ).fetchone()
            if row and row[0]:
                import re
                m = re.search(r"float\[(\d+)\]", row[0])
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return None

    def _try_init_plain_sqlite(self) -> bool:
        """Initialize a dependency-free SQLite fallback for persisted vectors."""
        global _use_tfidf_fallback
        try:
            import sqlite3

            db_path = _resolve_sqlite_path(self.settings.sqlite_db_path)
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT,
                    document_name TEXT,
                    text TEXT NOT NULL,
                    chunk_index INTEGER DEFAULT 0,
                    embedding TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_chunks_document ON rag_chunks(document_id)")
            conn.commit()

            self._sqlite_conn = conn
            self._ensure_document_registry_table()
            self._load_plain_sqlite_into_memory()
            _use_tfidf_fallback = True
            logger.info(f"Plain SQLite vector fallback initialized at {db_path}")
            return True
        except Exception as e:
            logger.warning(f"Plain SQLite vector fallback unavailable ({e}), using memory only")
            return False

    def _load_plain_sqlite_into_memory(self) -> None:
        try:
            rows = self._sqlite_conn.execute(
                """
                SELECT chunk_id, document_id, document_name, text, chunk_index, embedding
                FROM rag_chunks
                """
            ).fetchall()
        except Exception:
            return

        existing = {c["id"] for c in IN_MEMORY_INDEX}
        for row in rows:
            if row[0] in existing:
                continue
            try:
                embedding = np.array(json.loads(row[5]), dtype=np.float32)
            except Exception:
                continue
            IN_MEMORY_INDEX.append({
                "id": row[0],
                "document_id": row[1] or "",
                "document_name": row[2] or "",
                "text": row[3] or "",
                "chunk_index": row[4] or 0,
                "embedding": embedding,
            })

    async def _insert_plain_sqlite(self, chunks: list[dict], embeddings: list[list[float]]) -> None:
        rows = [
            (
                c["id"],
                c.get("document_id", ""),
                c.get("document_name", ""),
                c.get("text", ""),
                c.get("chunk_index", 0),
                json.dumps(emb),
            )
            for c, emb in zip(chunks, embeddings)
        ]

        loop = asyncio.get_running_loop()

        def _do_insert():
            self._sqlite_conn.executemany(
                """
                INSERT OR REPLACE INTO rag_chunks
                    (chunk_id, document_id, document_name, text, chunk_index, embedding)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._sqlite_conn.commit()

        await loop.run_in_executor(None, _do_insert)

    def _replace_plain_sqlite_from_memory(self) -> None:
        rows = [
            (
                c["id"],
                c.get("document_id", ""),
                c.get("document_name", ""),
                c.get("text", ""),
                c.get("chunk_index", 0),
                json.dumps(np.asarray(c["embedding"], dtype=np.float32).tolist()),
            )
            for c in IN_MEMORY_INDEX
        ]
        self._sqlite_conn.execute("DELETE FROM rag_chunks")
        self._sqlite_conn.executemany(
            """
            INSERT OR REPLACE INTO rag_chunks
                (chunk_id, document_id, document_name, text, chunk_index, embedding)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._sqlite_conn.commit()

    async def _search_plain_sqlite(self, query_vec: list[float], top_k: int) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()

        def _do_search():
            return self._sqlite_conn.execute(
                """
                SELECT chunk_id, document_id, document_name, text, chunk_index, embedding
                FROM rag_chunks
                """
            ).fetchall()

        rows = await loop.run_in_executor(None, _do_search)
        if not rows:
            return []

        query = np.array(query_vec, dtype=np.float32)
        scored: list[dict[str, Any]] = []
        for row in rows:
            try:
                vec = np.array(json.loads(row[5]), dtype=np.float32)
            except Exception:
                continue
            if vec.shape != query.shape:
                continue
            denom = (np.linalg.norm(vec) * np.linalg.norm(query)) + 1e-8
            score = float(np.dot(vec, query) / denom)
            if score <= 0.01:
                continue
            scored.append({
                "id": row[0],
                "document_id": row[1] or "",
                "document_name": row[2] or "",
                "text": row[3] or "",
                "chunk_index": row[4] or 0,
                "score": score,
            })

        return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]

    async def _delete_plain_sqlite(self, document_id: str) -> None:
        loop = asyncio.get_running_loop()

        def _do_delete():
            self._sqlite_conn.execute("DELETE FROM rag_chunks WHERE document_id = ?", (document_id,))
            self._sqlite_conn.execute("DELETE FROM rag_documents WHERE document_id = ?", (document_id,))
            self._sqlite_conn.commit()

        await loop.run_in_executor(None, _do_delete)

    def _ensure_document_registry_table(self) -> None:
        self._sqlite_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_documents (
                content_hash TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                document_name TEXT NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                index_statuses TEXT NOT NULL DEFAULT '{}',
                errors TEXT NOT NULL DEFAULT '[]',
                lifecycle_status TEXT NOT NULL DEFAULT 'enabled',
                status TEXT NOT NULL DEFAULT 'ready',
                job_id TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        for column, definition in (
            ("index_statuses", "TEXT NOT NULL DEFAULT '{}'"),
            ("errors", "TEXT NOT NULL DEFAULT '[]'"),
            ("lifecycle_status", "TEXT NOT NULL DEFAULT 'enabled'"),
            ("status", "TEXT NOT NULL DEFAULT 'ready'"),
            ("job_id", "TEXT NOT NULL DEFAULT ''"),
            ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
            ("max_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("last_error", "TEXT NOT NULL DEFAULT ''"),
            ("file_path", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                self._sqlite_conn.execute(f"ALTER TABLE rag_documents ADD COLUMN {column} {definition}")
            except Exception:
                pass
        self._sqlite_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rag_documents_document ON rag_documents(document_id)"
        )
        self._sqlite_conn.commit()

    def _merge_document_registry(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._sqlite_conn:
            return documents

        try:
            rows = self._sqlite_conn.execute(
                """
                SELECT
                    document_id, document_name, chunk_count, index_statuses, errors,
                    lifecycle_status, status, job_id, attempt_count, max_attempts,
                    last_error, updated_at
                FROM rag_documents
                """
            ).fetchall()
        except Exception:
            rows = []

        registry = {
            row[0]: {
                "document_name": row[1] or "unknown",
                "chunk_count": int(row[2] or 0),
                "index_statuses": _json_dict(row[3]),
                "errors": _json_list(row[4]),
                "lifecycle_status": _normalize_lifecycle_status(row[5] if len(row) > 5 else None),
                "status": _normalize_ingestion_status(row[6] if len(row) > 6 else None, _json_dict(row[3])),
                "job_id": str(row[7] or "") if len(row) > 7 else "",
                "attempt_count": int(row[8] or 0) if len(row) > 8 else 0,
                "max_attempts": int(row[9] or 0) if len(row) > 9 else 0,
                "last_error": str(row[10] or "") if len(row) > 10 else "",
                "updated_at": str(row[11] or "") if len(row) > 11 else "",
            }
            for row in rows
        }
        seen_document_ids = {doc.get("document_id") for doc in documents}

        for doc in documents:
            meta = registry.get(doc["document_id"])
            if meta:
                doc["document_name"] = doc.get("document_name") or meta["document_name"]
                doc["chunk_count"] = max(int(doc.get("chunk_count") or 0), meta["chunk_count"])
                doc["index_statuses"] = meta["index_statuses"] or {
                    "vector": "ready",
                    "bm25": "unknown",
                    "graph": "unknown",
                }
                doc["errors"] = meta["errors"]
                doc["lifecycle_status"] = meta["lifecycle_status"]
                doc["status"] = meta["status"]
                doc["job_id"] = meta["job_id"]
                doc["attempt_count"] = meta["attempt_count"]
                doc["max_attempts"] = meta["max_attempts"]
                doc["last_error"] = meta["last_error"]
                doc["updated_at"] = meta["updated_at"]
            else:
                doc["index_statuses"] = {"vector": "ready", "bm25": "unknown", "graph": "unknown"}
                doc["errors"] = []
                doc["lifecycle_status"] = DEFAULT_DOCUMENT_LIFECYCLE_STATUS
                doc["job_id"] = ""
                doc["status"] = _derive_document_status(doc["index_statuses"])
                doc["attempt_count"] = 0
                doc["max_attempts"] = 0
                doc["last_error"] = ""
                doc["updated_at"] = ""
            doc["is_retrievable"] = (
                doc["lifecycle_status"] == "enabled"
                and doc["status"] not in ACTIVE_DOCUMENT_STATUSES
                and doc["status"] not in {"error", "cancelled"}
            )

        for document_id, meta in registry.items():
            if document_id in seen_document_ids:
                continue
            documents.append({
                "document_id": document_id,
                "document_name": meta["document_name"],
                "chunk_count": meta["chunk_count"],
                "status": meta["status"],
                "job_id": meta["job_id"],
                "attempt_count": meta["attempt_count"],
                "max_attempts": meta["max_attempts"],
                "last_error": meta["last_error"],
                "updated_at": meta["updated_at"],
                "lifecycle_status": meta["lifecycle_status"],
                "is_retrievable": False,
                "index_statuses": meta["index_statuses"],
                "errors": meta["errors"],
            })

        return sorted(
            documents,
            key=lambda doc: (str(doc.get("document_name", "")).lower(), str(doc.get("document_id", ""))),
        )

    async def _count_plain_sqlite(self) -> int:
        loop = asyncio.get_running_loop()

        def _do_count():
            row = self._sqlite_conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()
            return row[0] if row else 0

        return await loop.run_in_executor(None, _do_count)

    async def _list_plain_sqlite_documents(self) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()

        def _do_list():
            rows = self._sqlite_conn.execute(
                """
                SELECT document_id, document_name, chunk_index, text
                FROM rag_chunks
                ORDER BY document_name, document_id, chunk_index
                """
            ).fetchall()
            return self._merge_document_registry(_documents_from_chunk_rows(rows))

        return await loop.run_in_executor(None, _do_list)

    async def _list_plain_sqlite_document_chunks(self, document_id: str) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()

        def _do_list():
            rows = self._sqlite_conn.execute(
                """
                SELECT chunk_id, document_id, document_name, chunk_index, text
                FROM rag_chunks
                WHERE document_id = ?
                ORDER BY chunk_index
                """,
                (document_id,),
            ).fetchall()
            return [
                {
                    "chunk_id": row[0],
                    "document_id": row[1] or "",
                    "document_name": row[2] or "",
                    "chunk_index": row[3] or 0,
                    "text": row[4] or "",
                }
                for row in rows
            ]

        return await loop.run_in_executor(None, _do_list)

    def _ensure_sqlite_table(self, dim: int) -> None:
        """Create vec0 virtual table, or recreate if dimension changed."""
        if self._sqlite_dim == dim:
            return

        collection = self.settings.milvus_collection
        if self._sqlite_dim is not None and self._sqlite_dim != dim:
            logger.info(f"Vector dimension changed ({self._sqlite_dim} → {dim}), recreating table")
            self._sqlite_conn.execute(f"DROP TABLE IF EXISTS [{collection}]")
            self._sqlite_conn.commit()

        self._sqlite_conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS [{collection}] USING vec0(
                chunk_id TEXT,
                embedding float[{dim}] distance_metric=cosine,
                document_id TEXT,
                document_name TEXT,
                chunk_index INTEGER,
                +text TEXT,
                chunk_size=1024
            )
        """)
        self._sqlite_conn.commit()
        self._sqlite_dim = dim

    async def _search_sqlite(self, query_vec: list[float], top_k: int) -> list[dict[str, Any]]:
        collection = self.settings.milvus_collection
        loop = asyncio.get_running_loop()

        def _do_search():
            rows = self._sqlite_conn.execute(
                f"""
                SELECT chunk_id, document_id, document_name, chunk_index, text, distance
                FROM [{collection}]
                WHERE embedding MATCH ?
                AND k = ?
                """,
                (json.dumps(query_vec), top_k),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "document_id": r[1],
                    "document_name": r[2],
                    "chunk_index": r[3],
                    "text": r[4],
                    "score": float(1.0 - r[5]),  # cosine distance → similarity
                }
                for r in rows
            ]

        return await loop.run_in_executor(None, _do_search)

    async def _insert_sqlite(self, chunks: list[dict], embeddings: list[list[float]]) -> None:
        dim = len(embeddings[0])
        self._ensure_sqlite_table(dim)

        collection = self.settings.milvus_collection
        rows = [
            (
                c["id"],
                c.get("document_id", ""),
                c.get("document_name", ""),
                c.get("chunk_index", 0),
                c.get("text", ""),
                json.dumps(emb),
            )
            for c, emb in zip(chunks, embeddings)
        ]

        loop = asyncio.get_running_loop()

        def _do_insert():
            self._sqlite_conn.executemany(
                f"""
                INSERT INTO [{collection}] (chunk_id, document_id, document_name, chunk_index, text, embedding)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._sqlite_conn.commit()

        await loop.run_in_executor(None, _do_insert)

    async def _delete_sqlite(self, document_id: str) -> None:
        collection = self.settings.milvus_collection
        loop = asyncio.get_running_loop()

        def _do_delete():
            self._sqlite_conn.execute(
                f"DELETE FROM [{collection}] WHERE document_id = ?",
                (document_id,),
            )
            self._sqlite_conn.execute("DELETE FROM rag_documents WHERE document_id = ?", (document_id,))
            self._sqlite_conn.commit()

        await loop.run_in_executor(None, _do_delete)

    async def _count_sqlite(self) -> int:
        collection = self.settings.milvus_collection
        loop = asyncio.get_running_loop()

        def _do_count():
            row = self._sqlite_conn.execute(
                f"SELECT COUNT(*) FROM [{collection}]"
            ).fetchone()
            return row[0] if row else 0

        return await loop.run_in_executor(None, _do_count)

    async def _list_sqlite_documents(self) -> list[dict[str, Any]]:
        collection = self.settings.milvus_collection
        loop = asyncio.get_running_loop()

        def _do_list():
            rows = self._sqlite_conn.execute(
                f"""
                SELECT document_id, document_name, chunk_index, text
                FROM [{collection}]
                ORDER BY document_name, document_id, chunk_index
                """
            ).fetchall()
            return self._merge_document_registry(_documents_from_chunk_rows(rows))

        return await loop.run_in_executor(None, _do_list)

    async def _list_sqlite_document_chunks(self, document_id: str) -> list[dict[str, Any]]:
        collection = self.settings.milvus_collection
        loop = asyncio.get_running_loop()

        def _do_list():
            rows = self._sqlite_conn.execute(
                f"""
                SELECT chunk_id, document_id, document_name, chunk_index, text
                FROM [{collection}]
                WHERE document_id = ?
                ORDER BY chunk_index
                """,
                (document_id,),
            ).fetchall()
            return [
                {
                    "chunk_id": row[0],
                    "document_id": row[1] or "",
                    "document_name": row[2] or "",
                    "chunk_index": row[3] or 0,
                    "text": row[4] or "",
                }
                for row in rows
            ]

        return await loop.run_in_executor(None, _do_list)

    # ------------------------------------------------------------------
    # In-memory search
    # ------------------------------------------------------------------

    async def _search_memory(self, query_vec: np.ndarray, top_k: int) -> list[dict[str, Any]]:
        async with _index_lock:
            if not IN_MEMORY_INDEX:
                return []
            db_vectors = np.stack([c["embedding"] for c in IN_MEMORY_INDEX])
            ids = [c["id"] for c in IN_MEMORY_INDEX]
            doc_ids = [c["document_id"] for c in IN_MEMORY_INDEX]
            doc_names = [c.get("document_name", "") for c in IN_MEMORY_INDEX]
            texts = [c["text"] for c in IN_MEMORY_INDEX]
            chunk_indices = [c.get("chunk_index", 0) for c in IN_MEMORY_INDEX]
        # Compute similarity outside the lock
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-8)
        db_norm = db_vectors / (np.linalg.norm(db_vectors, axis=1, keepdims=True) + 1e-8)
        scores = np.dot(db_norm, query_norm)
        top_indices = np.argsort(scores)[::-1][:top_k]

        return [
            {
                "id": ids[i],
                "document_id": doc_ids[i],
                "document_name": doc_names[i],
                "text": texts[i],
                "chunk_index": chunk_indices[i],
                "score": float(scores[i]),
            }
            for i in top_indices
            if scores[i] > 0.01
        ]

    # ------------------------------------------------------------------
    # Milvus (when available)
    # ------------------------------------------------------------------

    def _try_connect_milvus(self) -> bool:
        if self._use_milvus:
            return True
        try:
            from pymilvus import connections, utility
            connections.connect(
                alias="default",
                host=self.settings.milvus_host,
                port=self.settings.milvus_port,
                timeout=2,
            )
            if utility.has_collection(self.settings.milvus_collection):
                self._use_milvus = True
                logger.info("Connected to Milvus")
            else:
                connections.disconnect("default")
        except Exception:
            pass
        return self._use_milvus

    async def _search_milvus(self, query_vec: np.ndarray, top_k: int) -> list[dict[str, Any]]:
        from pymilvus import Collection
        col = Collection(self.settings.milvus_collection)
        col.load()
        results = col.search(
            data=[query_vec.tolist()],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k,
            output_fields=["id", "document_id", "document_name", "text", "chunk_index"],
        )
        return [
            {
                "id": hit.id,
                "document_id": hit.entity.get("document_id"),
                "document_name": hit.entity.get("document_name", ""),
                "text": hit.entity.get("text"),
                "chunk_index": hit.entity.get("chunk_index"),
                "score": float(hit.distance),
            }
            for hit in results[0]
        ]

    async def _insert_milvus(self, chunks: list[dict], embeddings: list[list[float]]) -> None:
        from pymilvus import Collection
        col = Collection(self.settings.milvus_collection)
        entities = [
            [c["id"] for c in chunks],
            [c["document_id"] for c in chunks],
            [c.get("document_name", "") for c in chunks],
            [c["text"] for c in chunks],
            [c.get("chunk_index", 0) for c in chunks],
            embeddings,
        ]
        col.insert(entities)
        col.flush()

    # ------------------------------------------------------------------
    # Embedding (sentence-transformers with TF-IDF fallback)
    # ------------------------------------------------------------------

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if _is_openai_embedding_model(self.settings.embedding_model):
            result = await self._embed_with_openai(texts)
            if result is not None:
                return result
            return await self._embed_with_tfidf(texts)

        if not _use_tfidf_fallback:
            model = _load_transformer_model()
            if model is not None:
                result = await self._embed_with_transformer(model, texts)
                if result is not None:
                    return result
        return await self._embed_with_tfidf(texts)

    async def _embed_with_openai(self, texts: list[str]) -> list[list[float]] | None:
        cleaned = [t.replace("\n", " ") for t in texts]
        try:
            resp = await get_llm_client().embeddings.create(
                model=self.settings.embedding_model,
                input=cleaned,
            )
            return [item.embedding for item in resp.data]
        except Exception as e:
            logger.warning(f"OpenAI-compatible embedding failed ({e}), falling back to TF-IDF")
            return None

    async def _embed_with_transformer(self, model, texts: list[str]) -> list[list[float]] | None:
        global _use_tfidf_fallback
        cleaned = [t.replace("\n", " ") for t in texts]
        loop = asyncio.get_running_loop()
        try:
            embeddings = await loop.run_in_executor(
                None,
                lambda: model.encode(cleaned, convert_to_numpy=True, show_progress_bar=False),
            )
            return embeddings.tolist()
        except Exception as e:
            logger.warning(f"Transformer encode failed ({e}), switching to TF-IDF fallback")
            _use_tfidf_fallback = True
            return None

    async def _embed_with_tfidf(self, texts: list[str]) -> list[list[float]]:
        global _tfidf_vectorizer, _tfidf_fitted
        cleaned = [t.replace("\n", " ") for t in texts]
        loop = asyncio.get_running_loop()

        if _tfidf_vectorizer is None:
            from sklearn.feature_extraction.text import TfidfVectorizer
            _tfidf_vectorizer = TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(2, 5),
                max_features=1024,
                sublinear_tf=True,
                norm="l2",
            )

        if not _tfidf_fitted and IN_MEMORY_INDEX:
            await loop.run_in_executor(None, _refit_tfidf)
        elif not _tfidf_fitted:
            await loop.run_in_executor(None, _tfidf_vectorizer.fit, cleaned)
            _tfidf_fitted = True

        matrix = await loop.run_in_executor(
            None, lambda: _tfidf_vectorizer.transform(cleaned).toarray()
        )
        norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8
        return (matrix / norms).tolist()


def _refit_tfidf() -> None:
    """Re-fit TF-IDF vectorizer on all stored documents and recompute embeddings."""
    global _tfidf_vectorizer, _tfidf_fitted
    if _tfidf_vectorizer is None:
        return
    texts = [c["text"] for c in IN_MEMORY_INDEX]
    if not texts:
        return
    logger.info(f"Refitting TF-IDF on {len(texts)} documents")
    _tfidf_vectorizer.fit(texts)
    _tfidf_fitted = True
    for c in IN_MEMORY_INDEX:
        vec = _tfidf_vectorizer.transform([c["text"]]).toarray()[0]
        norm = np.linalg.norm(vec) + 1e-8
        c["embedding"] = (vec / norm).astype(np.float32)


def _resolve_sqlite_path(path: str) -> str:
    db_path = Path(path)
    if db_path.is_absolute():
        return str(db_path)
    project_root = Path(__file__).resolve().parents[3]
    return str(project_root / db_path)


def _normalize_dedupe_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _chunk_text_signature(texts: list[str]) -> tuple[str, ...]:
    return tuple(text for text in (_normalize_dedupe_text(t) for t in texts) if text)


def _documents_from_chunk_rows(rows: list[tuple]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for document_id, document_name, chunk_index, text in rows:
        if not document_id:
            continue
        item = grouped.setdefault(
            document_id,
            {"document_id": document_id, "document_name": document_name or "unknown", "chunks": []},
        )
        item["chunks"].append((chunk_index or 0, text or ""))

    documents: list[dict[str, Any]] = []
    seen_signatures: set[tuple[str, ...]] = set()
    for item in grouped.values():
        ordered_texts = [text for _, text in sorted(item["chunks"], key=lambda pair: pair[0])]
        signature = _chunk_text_signature(ordered_texts)
        if signature and signature in seen_signatures:
            continue
        if signature:
            seen_signatures.add(signature)
        documents.append({
            "document_id": item["document_id"],
            "document_name": item["document_name"],
            "chunk_count": len(ordered_texts),
            "status": "ready",
            "lifecycle_status": DEFAULT_DOCUMENT_LIFECYCLE_STATUS,
            "is_retrievable": True,
            "index_statuses": {"vector": "ready", "bm25": "unknown", "graph": "unknown"},
            "errors": [],
        })

    return sorted(
        documents,
        key=lambda doc: (doc["document_name"].lower(), doc["document_id"]),
    )


def _json_dict(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _normalize_lifecycle_status(value: Any) -> str:
    status = str(value or DEFAULT_DOCUMENT_LIFECYCLE_STATUS).strip().lower()
    if status not in DOCUMENT_LIFECYCLE_STATUSES:
        return DEFAULT_DOCUMENT_LIFECYCLE_STATUS
    return status


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_ingestion_status(value: Any, index_statuses: dict[str, str] | None = None) -> str:
    status = str(value or "").strip().lower()
    if status in DOCUMENT_INGESTION_STATUSES:
        return status
    return _derive_document_status(index_statuses or {})


def _derive_document_status(index_statuses: dict[str, str]) -> str:
    statuses = {str(k): str(v) for k, v in (index_statuses or {}).items()}
    if statuses.get("ingestion") in ACTIVE_DOCUMENT_STATUSES:
        return statuses["ingestion"]
    if statuses.get("ingestion") == "cancelled":
        return "cancelled"
    if statuses.get("ingestion") == "error":
        return "error"
    if statuses.get("dedupe") == "duplicate":
        return "duplicate"
    if statuses.get("vector") == "ready" and not any(value == "error" for value in statuses.values()):
        return "ready"
    if any(value == "ready" for value in statuses.values()):
        return "partial"
    if any(value == "error" for value in statuses.values()):
        return "error"
    if any(value == "processing" for value in statuses.values()):
        return "processing"
    if any(value == "queued" for value in statuses.values()):
        return "queued"
    return "ready"


def _load_transformer_model():
    global _embedding_model, _use_tfidf_fallback
    if _embedding_model is not None:
        return _embedding_model
    if _use_tfidf_fallback:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        settings = get_settings()
        model_name = settings.embedding_model
        logger.info(f"Loading embedding model: {model_name}")
        _embedding_model = SentenceTransformer(model_name)
        return _embedding_model
    except Exception as e:
        logger.warning(f"Sentence-transformers unavailable ({e}), falling back to TF-IDF")
        _use_tfidf_fallback = True
        return None


def _is_openai_embedding_model(model_name: str) -> bool:
    return model_name.startswith("text-embedding-")
