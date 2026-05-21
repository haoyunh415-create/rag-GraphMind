import unittest
import os

from app.retrieval.fusion import FusionRanker
from app.core import config


class FusionRankerTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        for key in (
            "RERANKER_ENABLED",
            "RERANKER_ORIGINAL_WEIGHT",
            "RERANKER_QUERY_WEIGHT",
            "RERANKER_PHRASE_WEIGHT",
            "RERANKER_SOURCE_WEIGHT",
        ):
            os.environ.pop(key, None)
        config.get_settings.cache_clear()

    async def test_rank_tolerates_empty_text_and_source(self) -> None:
        os.environ["RERANKER_ENABLED"] = "false"
        config.get_settings.cache_clear()
        ranker = FusionRanker()

        ranked = await ranker.rank(
            "refund policy",
            [
                {"id": "empty-text", "text": None, "score": 0.8, "source": None},
                {"id": "good", "text": "Refund policy supports returns.", "score": 0.7, "source": "bm25"},
            ],
            limit=5,
        )

        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["retrieval_sources"], ["unknown"])
        self.assertEqual(ranked[0]["id"], "empty-text")

    async def test_reranker_promotes_direct_query_evidence(self) -> None:
        ranker = FusionRanker()

        ranked = await ranker.rank(
            "食品可以 7 天无理由退货吗",
            [
                {
                    "id": "weak-high-rank",
                    "text": "平台运营指标包括命中率、采纳率、响应时间和客户满意度。",
                    "score": 10.0,
                    "source": "vector",
                },
                {
                    "id": "direct-evidence",
                    "text": "食品、定制商品和已拆封贴身用品不适用 7 天无理由退货。",
                    "score": 1.0,
                    "source": "bm25",
                },
            ],
            limit=2,
        )

        self.assertEqual(ranked[0]["id"], "direct-evidence")
        self.assertGreater(ranked[0]["_rerank_score"], ranked[1]["_rerank_score"])
        self.assertIn("_rrf_score", ranked[0])

    async def test_reranker_penalizes_incomplete_distractor_evidence(self) -> None:
        ranker = FusionRanker()

        ranked = await ranker.rank(
            "Food packaging appears in one note, but what is the actual rule for food returns?",
            [
                {
                    "id": "distractor",
                    "text": (
                        "Distractor document. Food packaging, invoice title, and response time "
                        "may appear together here, but this document does not provide complete "
                        "return, invoice, support, shipping, or operations rules."
                    ),
                    "score": 10.0,
                    "source": "vector",
                },
                {
                    "id": "returns-rule",
                    "text": (
                        "Return policy. Food, custom goods, virtual goods, and opened "
                        "personal-care items do not support seven-day no-reason returns."
                    ),
                    "score": 1.0,
                    "source": "bm25",
                },
            ],
            limit=2,
        )

        self.assertEqual(ranked[0]["id"], "returns-rule")
        self.assertGreater(ranked[1]["_rerank_features"]["evidence_penalty"], 0)


if __name__ == "__main__":
    unittest.main()
