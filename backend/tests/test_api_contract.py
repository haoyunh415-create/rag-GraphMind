import unittest

from fastapi.testclient import TestClient

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


if __name__ == "__main__":
    unittest.main()
