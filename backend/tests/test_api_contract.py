import unittest
import os
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.core import config
from app.retrieval import vector_store


class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self._temp_dirs: list[tempfile.TemporaryDirectory] = []

    def tearDown(self) -> None:
        os.environ.pop("SQLITE_DB_PATH", None)
        os.environ.pop("MAX_UPLOAD_BYTES", None)
        os.environ.pop("EMBEDDING_MODEL", None)
        os.environ.pop("API_AUTH_TOKEN", None)
        os.environ.pop("INGESTION_QUEUE_MODE", None)
        os.environ.pop("INGESTION_QUEUE_DIR", None)
        os.environ.pop("INGESTION_DLQ_NAME", None)
        config.get_settings.cache_clear()
        vector_store.IN_MEMORY_INDEX.clear()
        vector_store._use_tfidf_fallback = False
        vector_store._tfidf_vectorizer = None
        vector_store._tfidf_fitted = False
        for temp_dir in self._temp_dirs:
            temp_dir.cleanup()

    def use_isolated_sqlite(self) -> None:
        temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._temp_dirs.append(temp_dir)
        os.environ["SQLITE_DB_PATH"] = os.path.join(temp_dir.name, "rag-test.db")
        os.environ["EMBEDDING_MODEL"] = "local-test"
        os.environ["INGESTION_QUEUE_MODE"] = "inline"
        os.environ["INGESTION_QUEUE_DIR"] = os.path.join(temp_dir.name, "ingestion-uploads")
        os.environ["INGESTION_DLQ_NAME"] = "rag:ingestion:test:dead"
        config.get_settings.cache_clear()

    def wait_for_document_status(self, document_id: str, allowed: set[str] | None = None) -> dict:
        allowed = allowed or {"ready", "partial", "duplicate"}
        last_row: dict | None = None
        for _ in range(20):
            response = self.client.get("/api/kb/documents")
            self.assertEqual(response.status_code, 200)
            rows = response.json()["documents"]
            last_row = next((item for item in rows if item["document_id"] == document_id), None)
            if last_row and last_row["status"] in allowed:
                return last_row
        self.fail(f"Document {document_id} did not reach {allowed}; last={last_row}")

    def test_health_endpoint(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "version": "0.1.0"})

    def test_api_auth_token_protects_business_endpoints(self) -> None:
        os.environ["API_AUTH_TOKEN"] = "test-secret"
        config.get_settings.cache_clear()

        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)

        missing = self.client.get("/api/kb/documents")
        self.assertEqual(missing.status_code, 401)

        wrong = self.client.get(
            "/api/kb/documents",
            headers={"Authorization": "Bearer wrong-secret"},
        )
        self.assertEqual(wrong.status_code, 403)

        authorized = self.client.get(
            "/api/kb/documents",
            headers={"Authorization": "Bearer test-secret"},
        )
        self.assertEqual(authorized.status_code, 200)

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

    def test_evaluation_endpoint_scores_and_persists_grounded_answer(self) -> None:
        self.use_isolated_sqlite()

        response = self.client.post(
            "/api/kb/evaluate",
            json={
                "query": "What does the stable demo flow do?",
                "answer": "The stable demo flow uploads, indexes, cites, and traces documents.",
                "contexts": [
                    {
                        "source": "vector",
                        "document_id": "doc-1",
                        "document_name": "demo.txt",
                        "chunk_id": "chunk-1",
                        "text": "The stable demo flow uploads, indexes, cites, and traces documents.",
                        "score": 0.92,
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["query"], "What does the stable demo flow do?")
        self.assertGreaterEqual(data["overall_score"], 0.6)
        self.assertGreaterEqual(data["groundedness"], 0.9)
        self.assertEqual(data["citation_count"], 1)
        self.assertIn(data["label"], {"pass", "warn"})

        evaluations = self.client.get("/api/kb/evaluations")
        self.assertEqual(evaluations.status_code, 200)
        rows = evaluations.json()["evaluations"]
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]["evaluation_id"], data["evaluation_id"])

    def test_upload_rejects_unsupported_extension(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            files={"file": ("payload.exe", b"not a document", "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 415)
        self.assertIn("Unsupported document type", response.json()["detail"])

    def test_upload_rejects_empty_document(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["detail"])

    def test_upload_rejects_oversized_document(self) -> None:
        config.get_settings.cache_clear()
        os.environ["MAX_UPLOAD_BYTES"] = "4"
        response = self.client.post(
            "/api/documents/upload",
            files={"file": ("large.txt", b"too large", "text/plain")},
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn("exceeds", response.json()["detail"])

    def test_upload_rejects_mime_extension_mismatch(self) -> None:
        response = self.client.post(
            "/api/documents/upload",
            files={"file": ("payload.pdf", b"not a real pdf", "text/plain")},
        )

        self.assertEqual(response.status_code, 415)
        self.assertIn("does not match", response.json()["detail"])

    def test_text_upload_list_chunks_and_delete_round_trip(self) -> None:
        self.use_isolated_sqlite()

        upload = self.client.post(
            "/api/documents/upload",
            files={
                "file": (
                    "alpha.txt",
                    b"Alpha project notes.\nThe stable demo flow uploads, indexes, cites, and traces documents.",
                    "text/plain",
                )
            },
        )

        self.assertEqual(upload.status_code, 200)
        upload_data = upload.json()
        self.assertEqual(upload_data["status"], "queued")
        self.assertTrue(upload_data["job_id"])
        document_id = upload_data["document_id"]

        document_row = self.wait_for_document_status(document_id)
        self.assertGreater(document_row["chunk_count"], 0)

        documents = self.client.get("/api/kb/documents")
        self.assertEqual(documents.status_code, 200)
        document_rows = documents.json()["documents"]
        document_ids = {item["document_id"] for item in document_rows}
        self.assertIn(document_id, document_ids)
        document_row = next(item for item in document_rows if item["document_id"] == document_id)
        self.assertEqual(document_row["lifecycle_status"], "enabled")
        self.assertTrue(document_row["is_retrievable"])

        status_update = self.client.patch(
            f"/api/documents/{document_id}/status",
            json={"lifecycle_status": "test"},
        )
        self.assertEqual(status_update.status_code, 200)
        self.assertEqual(status_update.json()["lifecycle_status"], "test")
        self.assertFalse(status_update.json()["is_retrievable"])

        documents_after_status = self.client.get("/api/kb/documents")
        updated_row = next(
            item for item in documents_after_status.json()["documents"] if item["document_id"] == document_id
        )
        self.assertEqual(updated_row["lifecycle_status"], "test")
        self.assertFalse(updated_row["is_retrievable"])

        chunks = self.client.get(f"/api/documents/{document_id}/chunks")
        self.assertEqual(chunks.status_code, 200)
        chunk_rows = chunks.json()["chunks"]
        self.assertGreaterEqual(len(chunk_rows), 1)
        self.assertIn("stable demo flow", chunk_rows[0]["text"])

        delete = self.client.delete(f"/api/documents/{document_id}")
        self.assertEqual(delete.status_code, 200)
        self.assertIn(delete.json()["status"], {"deleted", "partial"})

        chunks_after_delete = self.client.get(f"/api/documents/{document_id}/chunks")
        self.assertEqual(chunks_after_delete.status_code, 200)
        self.assertEqual(chunks_after_delete.json()["chunks"], [])

    def test_kb_mode_ignores_non_enabled_documents(self) -> None:
        self.use_isolated_sqlite()

        upload = self.client.post(
            "/api/documents/upload",
            files={
                "file": (
                    "disabled.txt",
                    b"Disabled document says the alpha refund policy is 30 days.",
                    "text/plain",
                )
            },
        )
        self.assertEqual(upload.status_code, 200)
        document_id = upload.json()["document_id"]
        self.wait_for_document_status(document_id)

        status_update = self.client.patch(
            f"/api/documents/{document_id}/status",
            json={"lifecycle_status": "disabled"},
        )
        self.assertEqual(status_update.status_code, 200)

        response = self.client.post(
            "/api/chat/stream",
            json={"query": "What is the alpha refund policy?", "mode": "kb", "top_k": 5},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"data": []', response.text)
        self.assertIn('"reason": "no_enabled_documents"', response.text)

    def test_can_cancel_queued_ingestion_job(self) -> None:
        self.use_isolated_sqlite()

        async def fake_enqueue(*args, **kwargs):
            return "redis"

        with patch("app.api.documents.enqueue_ingestion_job", fake_enqueue):
            upload = self.client.post(
                "/api/documents/upload",
                files={
                    "file": (
                        "queued.txt",
                        b"This document should remain queued until cancelled.",
                        "text/plain",
                    )
                },
            )

        self.assertEqual(upload.status_code, 200)
        document_id = upload.json()["document_id"]

        cancel = self.client.post(f"/api/documents/{document_id}/cancel")
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()["status"], "cancelled")
        self.assertFalse(cancel.json()["is_retrievable"])

        status = self.client.get(f"/api/documents/{document_id}/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "cancelled")
        self.assertFalse(status.json()["is_retrievable"])

        retry = self.client.post(f"/api/documents/{document_id}/retry")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["status"], "queued")
        retried_row = self.wait_for_document_status(document_id)
        self.assertIn(retried_row["status"], {"ready", "partial"})

    def test_retry_rejects_ready_document_and_queue_health_is_stable(self) -> None:
        self.use_isolated_sqlite()

        upload = self.client.post(
            "/api/documents/upload",
            files={
                "file": (
                    "ready.txt",
                    b"This document becomes ready and should not be retried.",
                    "text/plain",
                )
            },
        )
        self.assertEqual(upload.status_code, 200)
        document_id = upload.json()["document_id"]
        self.wait_for_document_status(document_id)

        retry = self.client.post(f"/api/documents/{document_id}/retry")
        self.assertEqual(retry.status_code, 409)

        health = self.client.get("/api/documents/ingestion/health")
        self.assertEqual(health.status_code, 200)
        data = health.json()
        self.assertEqual(data["mode"], "inline")
        self.assertIn("queue_length", data)
        self.assertIn("dead_letter_length", data)

    def test_chat_stream_emits_quality_evaluation_and_trace_step(self) -> None:
        self.use_isolated_sqlite()

        upload = self.client.post(
            "/api/documents/upload",
            files={
                "file": (
                    "flow.txt",
                    b"The stable demo flow uploads, indexes, cites, and traces documents.",
                    "text/plain",
                )
            },
        )
        self.assertEqual(upload.status_code, 200)
        self.wait_for_document_status(upload.json()["document_id"])

        async def fake_decompose_query(query: str) -> list[str]:
            return [query]

        async def fake_synthesize_answer(*args, **kwargs):
            yield "The stable demo flow uploads, indexes, cites, and traces documents."

        with (
            patch("app.agents.orchestrator.decompose_query", fake_decompose_query),
            patch("app.agents.orchestrator.synthesize_answer", fake_synthesize_answer),
        ):
            response = self.client.post(
                "/api/chat/stream",
                json={"query": "What does the stable demo flow do?", "mode": "kb", "top_k": 5},
            )

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn('"type": "evaluation"', body)
        self.assertIn('"overall_score"', body)
        self.assertIn('"name": "evaluate"', body)
        self.assertIn('"duration_ms"', body)
        self.assertIn('"timings"', body)

    def test_kb_stats_reports_real_local_storage_size(self) -> None:
        self.use_isolated_sqlite()

        upload = self.client.post(
            "/api/documents/upload",
            files={
                "file": (
                    "stats.txt",
                    b"Stats verification document. Storage size should reflect the local SQLite database.",
                    "text/plain",
                )
            },
        )
        self.assertEqual(upload.status_code, 200)
        self.wait_for_document_status(upload.json()["document_id"])

        stats = self.client.get("/api/kb/stats")
        self.assertEqual(stats.status_code, 200)
        data = stats.json()
        self.assertGreaterEqual(data["total_documents"], 1)
        self.assertGreaterEqual(data["total_chunks"], 1)
        self.assertGreater(data["storage_size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
