import os
import unittest

from app.core import config
from app.retrieval.citation_pruner import prune_citations, prune_citations_with_report


class CitationPrunerTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["CITATION_MAX_ITEMS"] = "3"
        os.environ["CITATION_PER_DOCUMENT_LIMIT"] = "2"
        os.environ["CITATION_MIN_RELATIVE_SCORE"] = "0.55"
        os.environ["CITATION_MIN_QUERY_COVERAGE"] = "0.35"
        config.get_settings.cache_clear()

    def tearDown(self) -> None:
        for key in (
            "CITATION_MAX_ITEMS",
            "CITATION_PER_DOCUMENT_LIMIT",
            "CITATION_MIN_RELATIVE_SCORE",
            "CITATION_MIN_QUERY_COVERAGE",
        ):
            os.environ.pop(key, None)
        config.get_settings.cache_clear()

    def test_prunes_weak_or_unrelated_citations(self) -> None:
        ranked = [
            {
                "id": "good-1",
                "document_id": "returns",
                "document_name": "退换货政策.md",
                "text": "食品、定制商品、虚拟商品和已拆封的贴身用品不适用 7 天无理由退货。",
                "score": 1.0,
                "source": "bm25",
            },
            {
                "id": "weak-1",
                "document_id": "metrics",
                "document_name": "运营指标.md",
                "text": "平台建议重点关注问题命中率、答案采纳率和转人工率。",
                "score": 0.9,
                "source": "vector",
            },
            {
                "id": "weak-2",
                "document_id": "invoice",
                "document_name": "发票流程.md",
                "text": "用户可以在订单完成后 30 天内申请电子发票。",
                "score": 0.2,
                "source": "bm25",
            },
        ]

        citations = prune_citations("食品可以 7 天无理由退货吗？", ranked)

        self.assertEqual([item["id"] for item in citations], ["good-1"])
        self.assertGreaterEqual(citations[0]["_citation_query_coverage"], 0.35)

        report = prune_citations_with_report("食品可以 7 天无理由退货吗？", ranked)
        rejected = {
            item["id"]: item.get("_citation_rejection_reason")
            for item in report["candidates"]
            if not item.get("_citation_selected")
        }
        self.assertEqual(rejected["weak-1"], "query_coverage_low")
        self.assertEqual(rejected["weak-2"], "query_coverage_low")

    def test_limits_citations_per_document_and_total_count(self) -> None:
        ranked = [
            {
                "id": f"doc-a-{index}",
                "document_id": "doc-a",
                "document_name": "退换货政策.md",
                "text": "质量问题商品支持签收后 15 天内申请退换货，商品质量问题运费由商家承担。",
                "score": 1.0 - index * 0.05,
                "source": "vector",
            }
            for index in range(4)
        ] + [
            {
                "id": "doc-b-1",
                "document_id": "doc-b",
                "document_name": "售后规则.md",
                "text": "商品破损、错发、漏发属于售后质量问题，运费由商家承担。",
                "score": 0.8,
                "source": "bm25",
            }
        ]

        citations = prune_citations("商品质量问题退换货运费由谁承担？", ranked)

        self.assertLessEqual(len(citations), 3)
        self.assertLessEqual(
            sum(1 for item in citations if item["document_id"] == "doc-a"),
            2,
        )
        self.assertIn("doc-b-1", {item["id"] for item in citations})

    def test_returns_no_citation_when_top_result_is_not_evidence(self) -> None:
        ranked = [
            {
                "id": "platform-only",
                "document_id": "overview",
                "document_name": "平台概述.md",
                "text": "星桥智能客服平台适用于电商、教育和企业服务场景。",
                "score": 1.0,
                "source": "vector",
            }
        ]

        citations = prune_citations("星桥平台的收费标准是多少？", ranked)

        self.assertEqual(citations, [])

    def test_rejects_incomplete_distractor_and_selects_rule_evidence(self) -> None:
        ranked = [
            {
                "id": "distractor",
                "document_id": "distractor",
                "document_name": "golden-distractor.txt",
                "text": (
                    "Distractor document. Food packaging, invoice title, and response time "
                    "may appear together here, but this document does not provide complete "
                    "return, invoice, support, shipping, or operations rules."
                ),
                "score": 1.0,
                "source": "vector",
            },
            {
                "id": "returns-rule",
                "document_id": "returns",
                "document_name": "golden-returns.txt",
                "text": (
                    "Return policy. Food, custom goods, virtual goods, and opened "
                    "personal-care items do not support seven-day no-reason returns."
                ),
                "score": 0.8,
                "source": "bm25",
            },
        ]

        report = prune_citations_with_report(
            "Food packaging appears in one note, but what is the actual rule for food returns?",
            ranked,
        )

        self.assertEqual([item["id"] for item in report["selected"]], ["returns-rule"])
        rejected = {
            item["id"]: item.get("_citation_rejection_reason")
            for item in report["candidates"]
            if not item.get("_citation_selected")
        }
        self.assertEqual(rejected["distractor"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
