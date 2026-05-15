import time
import functools
from typing import Callable
from loguru import logger

tracer = None
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    tracer_provider = TracerProvider()
    tracer = trace.get_tracer(__name__)
except Exception:
    pass


class RetrievalTrace:
    """Tracks a single retrieval: query decomposition -> searches -> fusion -> result."""

    def __init__(self, query_id: str, original_query: str):
        self.query_id = query_id
        self.original_query = original_query
        self.sub_queries: list[str] = []
        self.steps: list[dict] = []
        self.start_time = time.time()

    def add_step(self, name: str, detail: dict) -> None:
        self.steps.append({"name": name, "detail": detail, "elapsed_ms": (time.time() - self.start_time) * 1000})

    def to_dict(self) -> dict:
        # Flatten detail into step so the frontend can consume directly
        flat_steps: list[dict] = []
        for s in self.steps:
            flat = {"name": s["name"], "elapsed_ms": s["elapsed_ms"]}
            flat.update(s.get("detail", {}))
            flat_steps.append(flat)

        return {
            "query_id": self.query_id,
            "original_query": self.original_query,
            "sub_queries": self.sub_queries,
            "steps": flat_steps,
            "total_ms": (time.time() - self.start_time) * 1000,
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
