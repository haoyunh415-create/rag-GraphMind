import unittest
import os
import tempfile

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
        config.get_settings.cache_clear()

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

    def test_evaluation_endpoint_is_explicitly_disabled(self) -> None:
        response = self.client.post(
            "/api/kb/evaluate",
            json={"query": "What is in the knowledge base?"},
        )

        self.assertEqual(response.status_code, 501)
        self.assertIn("not enabled", response.json()["detail"])

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
        self.assertIn(upload_data["status"], {"ready", "partial"})
        self.assertGreater(upload_data["chunk_count"], 0)
        document_id = upload_data["document_id"]

        documents = self.client.get("/api/kb/documents")
        self.assertEqual(documents.status_code, 200)
        document_ids = {item["document_id"] for item in documents.json()["documents"]}
        self.assertIn(document_id, document_ids)

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


if __name__ == "__main__":
    unittest.main()
