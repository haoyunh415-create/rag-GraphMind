import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation import rag
from app.evaluation import store


class EvaluationMetricTests(unittest.TestCase):
    def test_overlap_score_counts_relevant_terms(self) -> None:
        score = rag._overlap_score("Graph RAG supports vector retrieval", "Vector retrieval is enabled")

        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_faithfulness_uses_context_overlap(self) -> None:
        score = rag._faithfulness(
            "### 直接回答\nVector retrieval is enabled",
            "The platform has vector retrieval and BM25 search.",
        )

        self.assertGreater(score, 0.0)

    def test_context_recall_uses_expected_answer_when_present(self) -> None:
        score = rag._context_recall(
            "Milvus stores vectors",
            "Milvus stores vectors for document chunks",
            "Where are vectors stored?",
        )

        self.assertEqual(score, 1.0)


class EvaluateQueryTests(unittest.TestCase):
    def test_evaluate_query_returns_non_placeholder_result(self) -> None:
        async def fake_decompose(query: str) -> list[str]:
            return [query]

        async def fake_retrieve(sub_queries: list[str], top_k: int) -> list[dict]:
            return [
                {
                    "id": "chunk-1",
                    "document_id": "doc-1",
                    "document_name": "test.md",
                    "text": "Graph RAG combines vector retrieval, BM25, and graph search.",
                    "score": 0.9,
                    "source": "vector",
                }
            ]

        async def fake_synthesize(query: str, contexts: list[dict], sub_queries: list[str]):
            yield "### 直接回答\nGraph RAG combines vector retrieval, BM25, and graph search."

        with (
            patch.object(rag, "decompose_query", side_effect=fake_decompose),
            patch.object(rag, "_retrieve_contexts", side_effect=fake_retrieve),
            patch.object(rag, "synthesize_answer", side_effect=fake_synthesize),
        ):
            result = asyncio.run(rag.evaluate_query("What does Graph RAG combine?"))

        self.assertEqual(result.query, "What does Graph RAG combine?")
        self.assertIn("Graph RAG", result.answer)
        self.assertGreater(result.faithfulness, 0.0)
        self.assertGreater(result.answer_relevancy, 0.0)
        self.assertGreater(result.context_precision, 0.0)
        self.assertGreater(result.latency_ms, 0.0)


class EvaluationStoreTests(unittest.TestCase):
    def test_save_and_list_evaluations(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "evals.db")
            result = rag.RagEvaluation(
                query="q",
                answer="a",
                faithfulness=0.1,
                answer_relevancy=0.2,
                context_recall=0.3,
                context_precision=0.4,
                latency_ms=5.0,
            )
            with patch.object(store, "_resolve_db_path", return_value=db_path):
                row_id = asyncio.run(store.save_evaluation(result, expected_answer="expected"))
                rows = asyncio.run(store.list_evaluations(limit=10))

        self.assertEqual(row_id, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["query"], "q")
        self.assertEqual(rows[0]["expected_answer"], "expected")
        self.assertEqual(rows[0]["faithfulness"], 0.1)


if __name__ == "__main__":
    unittest.main()
