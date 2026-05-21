import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ingestion import queue


class FakeRedis:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.cancelled: set[str] = set()

    async def sismember(self, key: str, value: str) -> bool:
        return value in self.cancelled

    async def rpush(self, key: str, value: str) -> None:
        self.pushed.append((key, value))

    async def llen(self, key: str) -> int:
        return sum(1 for queue_key, _ in self.pushed if queue_key == key)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        rows = [value for queue_key, value in self.pushed if queue_key == key]
        return rows[start : end + 1]


class IngestionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_job_is_requeued_until_max_attempts(self) -> None:
        redis = FakeRedis()
        raw = json.dumps(
            {
                "file_path": "queued.txt",
                "document_id": "doc-1",
                "filename": "queued.txt",
                "content_hash": "hash-1",
                "job_id": "job-1",
                "attempt": 1,
            }
        )
        settings = SimpleNamespace(
            ingestion_max_attempts=2,
            ingestion_retry_delay_seconds=0,
            ingestion_queue_name="rag:ingestion:test",
            ingestion_dlq_name="",
        )

        async def fake_run(*args, **kwargs):
            return "error"

        with (
            patch("app.ingestion.queue.get_settings", return_value=settings),
            patch("app.ingestion.queue.run_ingestion_job", fake_run),
            patch("app.ingestion.queue.cleanup_ingestion_file") as cleanup,
        ):
            await queue._handle_raw_job(redis, raw)

        self.assertEqual(cleanup.call_count, 0)
        self.assertEqual(len(redis.pushed), 1)
        _, payload = redis.pushed[0]
        self.assertEqual(json.loads(payload)["attempt"], 2)

    async def test_terminal_job_cleans_up_upload_file(self) -> None:
        redis = FakeRedis()
        raw = json.dumps(
            {
                "file_path": "ready.txt",
                "document_id": "doc-2",
                "filename": "ready.txt",
                "content_hash": "hash-2",
                "job_id": "job-2",
                "attempt": 1,
            }
        )
        settings = SimpleNamespace(
            ingestion_max_attempts=2,
            ingestion_retry_delay_seconds=0,
            ingestion_queue_name="rag:ingestion:test",
            ingestion_dlq_name="",
        )

        async def fake_run(*args, **kwargs):
            return "ready"

        with (
            patch("app.ingestion.queue.get_settings", return_value=settings),
            patch("app.ingestion.queue.run_ingestion_job", fake_run),
            patch("app.ingestion.queue.cleanup_ingestion_file") as cleanup,
        ):
            await queue._handle_raw_job(redis, raw)

        cleanup.assert_called_once_with("ready.txt")
        self.assertEqual(redis.pushed, [])

    async def test_failed_terminal_job_moves_to_dead_letter_queue(self) -> None:
        redis = FakeRedis()
        raw = json.dumps(
            {
                "file_path": "failed.txt",
                "document_id": "doc-3",
                "filename": "failed.txt",
                "content_hash": "hash-3",
                "job_id": "job-3",
                "attempt": 2,
            }
        )
        settings = SimpleNamespace(
            ingestion_max_attempts=2,
            ingestion_retry_delay_seconds=0,
            ingestion_queue_name="rag:ingestion:test",
            ingestion_dlq_name="",
        )

        async def fake_run(*args, **kwargs):
            return "error"

        with (
            patch("app.ingestion.queue.get_settings", return_value=settings),
            patch("app.ingestion.queue.run_ingestion_job", fake_run),
            patch("app.ingestion.queue.cleanup_ingestion_file") as cleanup,
        ):
            await queue._handle_raw_job(redis, raw)

        self.assertEqual(cleanup.call_count, 0)
        self.assertEqual(len(redis.pushed), 1)
        dlq_name, payload = redis.pushed[0]
        self.assertEqual(dlq_name, "rag:ingestion:test:dead")
        self.assertEqual(json.loads(payload)["document_id"], "doc-3")
        self.assertIn("last_error", json.loads(payload))

    async def test_inline_queue_health_has_stable_schema(self) -> None:
        settings = SimpleNamespace(
            ingestion_queue_mode="inline",
            ingestion_queue_name="rag:ingestion:test",
            ingestion_dlq_name="",
        )

        with patch("app.ingestion.queue.get_settings", return_value=settings):
            health = await queue.get_ingestion_queue_health()

        self.assertEqual(health["mode"], "inline")
        self.assertEqual(health["queue_length"], 0)
        self.assertEqual(health["dead_letter_length"], 0)
        self.assertFalse(health["redis_available"])


if __name__ == "__main__":
    unittest.main()
