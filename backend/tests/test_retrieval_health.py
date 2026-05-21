import os
import unittest
from unittest.mock import patch

from app.core import config
from app.retrieval import health


class RetrievalHealthCacheTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        for key in (
            "RETRIEVAL_HEALTH_CACHE_SECONDS",
            "ES_HOST",
            "NEO4J_URI",
        ):
            os.environ.pop(key, None)
        config.get_settings.cache_clear()
        health.clear_retrieval_health_cache()

    async def test_reuses_health_probe_inside_ttl(self) -> None:
        os.environ["RETRIEVAL_HEALTH_CACHE_SECONDS"] = "60"
        config.get_settings.cache_clear()
        health.clear_retrieval_health_cache()
        calls: list[tuple[str, int]] = []

        async def fake_can_open(host: str, port: int, timeout: float = 0.4) -> bool:
            calls.append((host, port))
            return port == 9200

        with patch("app.retrieval.health._can_open", fake_can_open):
            first = await health.retrieval_health()
            second = await health.retrieval_health()

        self.assertTrue(first["bm25"]["available"])
        self.assertFalse(first["graph"]["available"])
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 2)

    async def test_force_refresh_bypasses_cache(self) -> None:
        os.environ["RETRIEVAL_HEALTH_CACHE_SECONDS"] = "60"
        config.get_settings.cache_clear()
        health.clear_retrieval_health_cache()
        calls = 0

        async def fake_can_open(host: str, port: int, timeout: float = 0.4) -> bool:
            nonlocal calls
            calls += 1
            return True

        with patch("app.retrieval.health._can_open", fake_can_open):
            await health.retrieval_health()
            await health.retrieval_health(force_refresh=True)

        self.assertEqual(calls, 4)

    async def test_zero_ttl_disables_cache(self) -> None:
        os.environ["RETRIEVAL_HEALTH_CACHE_SECONDS"] = "0"
        config.get_settings.cache_clear()
        health.clear_retrieval_health_cache()
        calls = 0

        async def fake_can_open(host: str, port: int, timeout: float = 0.4) -> bool:
            nonlocal calls
            calls += 1
            return False

        with patch("app.retrieval.health._can_open", fake_can_open):
            await health.retrieval_health()
            await health.retrieval_health()

        self.assertEqual(calls, 4)


if __name__ == "__main__":
    unittest.main()
