import os
import unittest
from unittest.mock import patch

from app.core import config
from app.core.observability import RetrievalTrace


class RetrievalTraceTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("TRACE_STEP_BUDGETS_MS", None)
        config.get_settings.cache_clear()

    def test_steps_include_segment_duration_and_timing_summary(self) -> None:
        with patch(
            "app.core.observability.time.perf_counter",
            side_effect=[100.0, 100.025, 100.100, 100.120],
        ):
            trace = RetrievalTrace(query_id="q-1", original_query="hello")
            trace.add_step("intent", {"intent": "kb"})
            trace.add_step("rank", {"output_count": 3})
            payload = trace.to_dict()

        first, second = payload["steps"]
        self.assertAlmostEqual(first["started_ms"], 0.0, places=3)
        self.assertAlmostEqual(first["duration_ms"], 25.0, places=3)
        self.assertAlmostEqual(first["elapsed_ms"], 25.0, places=3)
        self.assertAlmostEqual(second["started_ms"], 25.0, places=3)
        self.assertAlmostEqual(second["duration_ms"], 75.0, places=3)
        self.assertAlmostEqual(second["elapsed_ms"], 100.0, places=3)
        self.assertAlmostEqual(payload["total_ms"], 120.0, places=3)
        self.assertAlmostEqual(payload["timings"]["accounted_ms"], 100.0, places=3)
        self.assertAlmostEqual(payload["timings"]["untracked_ms"], 20.0, places=3)
        self.assertEqual(payload["timings"]["slowest_step"]["name"], "rank")

    def test_step_budget_warning_is_added_to_step_and_summary(self) -> None:
        os.environ["TRACE_STEP_BUDGETS_MS"] = "rank=50,cite=25"
        config.get_settings.cache_clear()

        with patch(
            "app.core.observability.time.perf_counter",
            side_effect=[200.0, 200.120, 200.125],
        ):
            trace = RetrievalTrace(query_id="q-2", original_query="slow")
            trace.add_step("rank", {"output_count": 3})
            payload = trace.to_dict()

        warning = payload["steps"][0]["performance_warnings"][0]
        self.assertEqual(warning["code"], "step_budget_exceeded")
        self.assertEqual(warning["step"], "rank")
        self.assertEqual(warning["severity"], "slow")
        self.assertAlmostEqual(warning["budget_ms"], 50.0, places=3)
        self.assertAlmostEqual(warning["duration_ms"], 120.0, places=3)
        self.assertEqual(payload["timings"]["performance_warning_count"], 1)
        self.assertEqual(payload["timings"]["performance_warnings"][0]["step"], "rank")


if __name__ == "__main__":
    unittest.main()
