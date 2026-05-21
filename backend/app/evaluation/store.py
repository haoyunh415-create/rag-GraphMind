from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.config import get_settings
from app.models.schemas import EvaluationResult


class EvaluationStore:
    """Persist RAG evaluation results in the local SQLite store."""

    def __init__(self, db_path: str | None = None):
        self.db_path = _resolve_sqlite_path(db_path or get_settings().sqlite_db_path)

    async def save(
        self,
        result: EvaluationResult,
        *,
        contexts: list[dict[str, Any]] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        loop = asyncio.get_running_loop()
        payload = result.model_dump()
        contexts_payload = _summarize_contexts(contexts or [])
        trace_payload = trace or {}

        def _save() -> None:
            with self._connect() as conn:
                self._ensure_table(conn)
                columns = [
                    "evaluation_id",
                    "query_id",
                    "conversation_id",
                    "query",
                    "answer",
                    "overall_score",
                    "label",
                    "metrics_json",
                    "issues_json",
                    "contexts_json",
                    "trace_json",
                    "created_at",
                ]
                values: list[Any] = [
                    result.evaluation_id,
                    result.query_id,
                    result.conversation_id,
                    result.query,
                    result.answer,
                    result.overall_score,
                    result.label,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(result.issues, ensure_ascii=False),
                    json.dumps(contexts_payload, ensure_ascii=False),
                    json.dumps(trace_payload, ensure_ascii=False),
                    result.created_at,
                ]
                existing_columns = self._column_names(conn)
                legacy_values = {
                    "expected_answer": "",
                    "faithfulness": result.faithfulness,
                    "answer_relevancy": result.answer_relevancy,
                    "context_recall": result.context_recall,
                    "context_precision": result.context_precision,
                    "latency_ms": result.latency_ms,
                }
                for column, value in legacy_values.items():
                    if column in existing_columns and column not in columns:
                        columns.append(column)
                        values.append(value)

                placeholders = ", ".join("?" for _ in columns)
                conn.execute(
                    f"""
                    INSERT OR REPLACE INTO rag_evaluations (
                        {", ".join(columns)}
                    )
                    VALUES ({placeholders})
                    """,
                    tuple(values),
                )
                conn.commit()

        try:
            await loop.run_in_executor(None, _save)
        except Exception as exc:
            logger.warning(f"Failed to save RAG evaluation: {exc}")
        return result

    async def list_recent(self, limit: int = 20) -> list[EvaluationResult]:
        safe_limit = max(1, min(limit, 100))
        loop = asyncio.get_running_loop()

        def _list() -> list[EvaluationResult]:
            with self._connect() as conn:
                self._ensure_table(conn)
                rows = conn.execute(
                    """
                    SELECT metrics_json
                    FROM rag_evaluations
                    ORDER BY datetime(created_at) DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
            results: list[EvaluationResult] = []
            for (raw,) in rows:
                try:
                    results.append(EvaluationResult(**json.loads(raw)))
                except Exception as exc:
                    logger.warning(f"Skipping invalid evaluation row: {exc}")
            return results

        return await loop.run_in_executor(None, _list)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _column_names(conn: sqlite3.Connection) -> set[str]:
        return {
            row[1]
            for row in conn.execute("PRAGMA table_info(rag_evaluations)").fetchall()
        }

    @staticmethod
    def _ensure_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                query_id TEXT,
                conversation_id TEXT,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                overall_score REAL NOT NULL,
                label TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                issues_json TEXT NOT NULL DEFAULT '[]',
                contexts_json TEXT NOT NULL DEFAULT '[]',
                trace_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        existing_columns = EvaluationStore._column_names(conn)
        required_columns = {
            "evaluation_id": "TEXT",
            "query_id": "TEXT",
            "conversation_id": "TEXT",
            "query": "TEXT NOT NULL DEFAULT ''",
            "answer": "TEXT NOT NULL DEFAULT ''",
            "overall_score": "REAL NOT NULL DEFAULT 0",
            "label": "TEXT NOT NULL DEFAULT 'fail'",
            "metrics_json": "TEXT NOT NULL DEFAULT '{}'",
            "issues_json": "TEXT NOT NULL DEFAULT '[]'",
            "contexts_json": "TEXT NOT NULL DEFAULT '[]'",
            "trace_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in required_columns.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE rag_evaluations ADD COLUMN {column} {definition}")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_evaluations_evaluation_id
            ON rag_evaluations(evaluation_id)
            WHERE evaluation_id IS NOT NULL
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_evaluations_query_id ON rag_evaluations(query_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_evaluations_created_at ON rag_evaluations(created_at)")


def _resolve_sqlite_path(path: str) -> Path:
    db_path = Path(path)
    if db_path.is_absolute():
        return db_path
    project_root = Path(__file__).resolve().parents[3]
    return project_root / db_path


def _summarize_contexts(contexts: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for ctx in contexts[:limit]:
        text = str(ctx.get("text") or "").replace("\n", " ")
        if len(text) > 300:
            text = text[:300].rstrip() + "..."
        summary.append({
            "source": ctx.get("source", "unknown"),
            "document_id": ctx.get("document_id", ""),
            "document_name": ctx.get("document_name", ""),
            "chunk_id": ctx.get("chunk_id") or ctx.get("id") or "",
            "chunk_index": ctx.get("chunk_index"),
            "score": ctx.get("score", 0.0),
            "text": text,
        })
    return summary
