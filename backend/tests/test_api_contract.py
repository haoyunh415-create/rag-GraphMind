import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_endpoint(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "version": "0.1.0"})

    def test_chat_request_rejects_empty_query(self) -> None:
        response = self.client.post(
            "/api/chat/stream",
            json={"query": "", "mode": "auto", "top_k": 10},
        )

        self.assertEqual(response.status_code, 422)

    def test_chat_request_rejects_invalid_mode(self) -> None:
        response = self.client.post(
            "/api/chat/stream",
            json={"query": "hello", "mode": "invalid", "top_k": 10},
        )

        self.assertEqual(response.status_code, 422)

    def test_chat_request_rejects_top_k_out_of_range(self) -> None:
        response = self.client.post(
            "/api/chat/stream",
            json={"query": "hello", "mode": "auto", "top_k": 100},
        )

        self.assertEqual(response.status_code, 422)

    def test_evaluate_endpoint_returns_quality_metrics(self) -> None:
        async def fake_evaluate_query(query: str, expected_answer: str | None = None):
            return SimpleNamespace(
                query=query,
                answer=f"answer for {query}",
                faithfulness=0.8,
                answer_relevancy=0.7,
                context_recall=0.6,
                context_precision=0.5,
                latency_ms=12.3,
            )

        with patch("app.api.knowledge_base.evaluate_query", side_effect=fake_evaluate_query):
            with patch("app.api.knowledge_base.save_evaluation", return_value=123) as save_mock:
                response = self.client.post(
                    "/api/kb/evaluate",
                    json={"query": "what is indexed?", "expected_answer": "documents"},
                )

        self.assertEqual(response.status_code, 200)
        save_mock.assert_called_once()
        data = response.json()
        self.assertEqual(data["query"], "what is indexed?")
        self.assertEqual(data["answer"], "answer for what is indexed?")
        self.assertEqual(data["faithfulness"], 0.8)
        self.assertEqual(data["answer_relevancy"], 0.7)
        self.assertEqual(data["context_recall"], 0.6)
        self.assertEqual(data["context_precision"], 0.5)
        self.assertEqual(data["latency_ms"], 12.3)

    def test_evaluation_history_endpoint_returns_records(self) -> None:
        async def fake_list_evaluations(limit: int = 50):
            return [
                {
                    "id": 1,
                    "created_at": "2026-05-17 00:00:00",
                    "query": "hello",
                    "expected_answer": None,
                    "answer": "world",
                    "faithfulness": 0.1,
                    "answer_relevancy": 0.2,
                    "context_recall": 0.3,
                    "context_precision": 0.4,
                    "latency_ms": 5.0,
                }
            ]

        with patch("app.api.knowledge_base.list_evaluations", side_effect=fake_list_evaluations):
            response = self.client.get("/api/kb/evaluations?limit=1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["evaluations"]), 1)
        self.assertEqual(data["evaluations"][0]["query"], "hello")

    def test_upload_rejects_unsupported_extension(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            files={"file": ("bad.exe", b"not allowed", "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 400)

    def test_upload_rejects_empty_file(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
