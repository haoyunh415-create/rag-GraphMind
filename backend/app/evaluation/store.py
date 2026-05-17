from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.evaluation.rag import RagEvaluation


def _resolve_db_path() -> str:
    settings = get_settings()
    db_path = Path(settings.sqlite_db_path)
    if db_path.is_absolute():
        return str(db_path)
    project_root = Path(__file__).resolve().parents[3]
    return str(project_root / db_path)


def _connect() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rag_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            query TEXT NOT NULL,
            expected_answer TEXT,
            answer TEXT NOT NULL,
            faithfulness REAL NOT NULL,
            answer_relevancy REAL NOT NULL,
            context_recall REAL NOT NULL,
            context_precision REAL NOT NULL,
            latency_ms REAL NOT NULL
        )
        """
    )
    conn.commit()
    return conn


async def save_evaluation(result: RagEvaluation, expected_answer: str | None = None) -> int:
    def _save() -> int:
        conn = _connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO rag_evaluations (
                    query,
                    expected_answer,
                    answer,
                    faithfulness,
                    answer_relevancy,
                    context_recall,
                    context_precision,
                    latency_ms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.query,
                    expected_answer,
                    result.answer,
                    result.faithfulness,
                    result.answer_relevancy,
                    result.context_recall,
                    result.context_precision,
                    result.latency_ms,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    return await asyncio.to_thread(_save)


async def list_evaluations(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))

    def _list() -> list[dict[str, Any]]:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    id,
                    created_at,
                    query,
                    expected_answer,
                    answer,
                    faithfulness,
                    answer_relevancy,
                    context_recall,
                    context_precision,
                    latency_ms
                FROM rag_evaluations
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "query": row[2],
                "expected_answer": row[3],
                "answer": row[4],
                "faithfulness": row[5],
                "answer_relevancy": row[6],
                "context_recall": row[7],
                "context_precision": row[8],
                "latency_ms": row[9],
            }
            for row in rows
        ]

    return await asyncio.to_thread(_list)
