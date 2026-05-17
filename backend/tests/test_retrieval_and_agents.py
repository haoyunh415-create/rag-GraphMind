import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.tools import classify_intent
from app.retrieval.fusion import FusionRanker


class FusionRankerTests(unittest.TestCase):
    def test_rank_deduplicates_same_text_across_sources(self) -> None:
        ranker = FusionRanker()
        results = [
            {"id": "v1", "source": "vector", "text": "Same fact", "score": 0.9},
            {"id": "b1", "source": "bm25", "text": "Same fact", "score": 0.7},
            {"id": "g1", "source": "graph", "text": "Another fact", "score": 0.8},
        ]

        ranked = asyncio.run(ranker.rank("fact", results, limit=10))

        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["text"], "Same fact")
        self.assertCountEqual(ranked[0]["retrieval_sources"], ["vector", "bm25"])

    def test_rank_respects_limit(self) -> None:
        ranker = FusionRanker()
        results = [
            {"id": str(i), "source": "vector", "text": f"fact {i}", "score": 1.0}
            for i in range(5)
        ]

        ranked = asyncio.run(ranker.rank("fact", results, limit=2))

        self.assertEqual(len(ranked), 2)


class AgentToolTests(unittest.TestCase):
    def test_classify_intent_detects_chat_without_llm(self) -> None:
        self.assertEqual(asyncio.run(classify_intent("你好")), "chat")

    def test_classify_intent_detects_knowledge_base_keywords(self) -> None:
        self.assertEqual(asyncio.run(classify_intent("根据知识库里的文档总结一下")), "kb")


if __name__ == "__main__":
    unittest.main()
