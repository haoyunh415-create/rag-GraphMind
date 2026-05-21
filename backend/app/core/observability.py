import time
import functools
from typing import Callable
from loguru import logger

from app.core.config import get_settings

tracer = None
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    tracer_provider = TracerProvider()
    tracer = trace.get_tracer(__name__)
except Exception:
    pass


def _parse_step_budgets(raw: str) -> dict[str, float]:
    budgets: dict[str, float] = {}
    for item in raw.split(","):
        if not item.strip() or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name = name.strip()
        try:
            budget_ms = float(value.strip())
        except ValueError:
            continue
        if name and budget_ms > 0:
            budgets[name] = budget_ms
    return budgets


def _performance_warning(name: str, duration_ms: float) -> dict | None:
    budgets = _parse_step_budgets(get_settings().trace_step_budgets_ms)
    budget_ms = budgets.get(name)
    if not budget_ms or duration_ms <= budget_ms:
        return None
    over_by_ms = duration_ms - budget_ms
    return {
        "code": "step_budget_exceeded",
        "step": name,
        "severity": "warn" if duration_ms <= budget_ms * 2 else "slow",
        "duration_ms": duration_ms,
        "budget_ms": budget_ms,
        "over_by_ms": over_by_ms,
    }


class RetrievalTrace:
    """Tracks a single retrieval: query decomposition -> searches -> fusion -> result."""

    def __init__(self, query_id: str, original_query: str):
        self.query_id = query_id
        self.original_query = original_query
        self.sub_queries: list[str] = []
        self.steps: list[dict] = []
        self.start_time = time.perf_counter()
        self._last_step_time = self.start_time

    def add_step(self, name: str, detail: dict) -> None:
        now = time.perf_counter()
        elapsed_ms = (now - self.start_time) * 1000
        duration_ms = (now - self._last_step_time) * 1000
        started_ms = (self._last_step_time - self.start_time) * 1000
        detail = dict(detail)
        warning = _performance_warning(name, duration_ms)
        if warning:
            detail["performance_warnings"] = [
                *detail.get("performance_warnings", []),
                warning,
            ]
        self.steps.append({
            "name": name,
            "detail": detail,
            "started_ms": started_ms,
            "elapsed_ms": elapsed_ms,
            "duration_ms": duration_ms,
        })
        self._last_step_time = now

    def to_dict(self) -> dict:
        # Flatten detail into step so the frontend can consume directly
        flat_steps: list[dict] = []
        for s in self.steps:
            flat = {
                "name": s["name"],
                "started_ms": s["started_ms"],
                "elapsed_ms": s["elapsed_ms"],
                "duration_ms": s["duration_ms"],
            }
            flat.update(s.get("detail", {}))
            flat_steps.append(flat)

        total_ms = (time.perf_counter() - self.start_time) * 1000
        accounted_ms = sum(step["duration_ms"] for step in flat_steps)
        slowest_step = max(
            flat_steps,
            key=lambda step: step.get("duration_ms", 0),
            default=None,
        )
        performance_warnings = [
            warning
            for step in flat_steps
            for warning in step.get("performance_warnings", [])
        ]

        return {
            "query_id": self.query_id,
            "original_query": self.original_query,
            "sub_queries": self.sub_queries,
            "steps": flat_steps,
            "total_ms": total_ms,
            "timings": {
                "accounted_ms": accounted_ms,
                "untracked_ms": max(total_ms - accounted_ms, 0),
                "slowest_step": {
                    "name": slowest_step["name"],
                    "duration_ms": slowest_step["duration_ms"],
                } if slowest_step else None,
                "performance_warnings": performance_warnings,
                "performance_warning_count": len(performance_warnings),
            },
        }


def traced(name: str):
    """Decorator that logs timing and adds span to trace (when OTel is available)."""
    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if tracer is None:
                start = time.time()
                try:
                    result = await fn(*args, **kwargs)
                    elapsed = (time.time() - start) * 1000
                    logger.info(f"[{name}] completed in {elapsed:.1f}ms")
                    return result
                except Exception as e:
                    logger.error(f"[{name}] failed: {e}")
                    raise

            with tracer.start_as_current_span(name) as span:
                start = time.time()
                try:
                    result = await fn(*args, **kwargs)
                    elapsed = (time.time() - start) * 1000
                    logger.info(f"[{name}] completed in {elapsed:.1f}ms")
                    span.set_attribute("elapsed_ms", elapsed)
                    return result
                except Exception as e:
                    span.record_exception(e)
                    logger.error(f"[{name}] failed: {e}")
                    raise
        return wrapper
    return decorator
