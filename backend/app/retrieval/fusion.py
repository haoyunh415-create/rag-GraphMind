import hashlib
import re

from app.core.config import get_settings


class FusionRanker:
    """Reciprocal rank fusion + optional cross-encoder rerank."""

    def __init__(self):
        self.settings = get_settings()

    async def rank(self, query: str, results: list[dict], limit: int | None = None) -> list[dict]:
        """Fuse results from multiple sources, dedup, and rerank via RRF."""
        deduped = self._reciprocal_rank_fusion(results)
        return deduped[: (limit or self.settings.rerank_top_k)]

    @staticmethod
    def _dedupe_key(item: dict) -> str:
        """Prefer content identity so duplicate uploads don't duplicate citations."""
        text = re.sub(r"\s+", " ", item.get("text", "")).strip().lower()
        if text:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return f"text:{digest}"
        return f"id:{item.get('id', '')}"

    def _reciprocal_rank_fusion(self, results: list[dict], k: int = 60) -> list[dict]:
        """RRF: score = sum(1 / (k + rank_in_source)) across sources, then dedup."""
        # Separate results by source
        by_source: dict[str, list[dict]] = {}
        for r in results:
            source = r.get("source", "unknown")
            by_source.setdefault(source, []).append(r)

        # Accumulate RRF scores per normalized content key.
        merged: dict[str, dict] = {}
        for items in by_source.values():
            for rank, item in enumerate(items, start=1):
                key = self._dedupe_key(item)
                rrf_score = 1.0 / (k + rank)
                if key not in merged:
                    merged[key] = dict(item)
                    merged[key]["score"] = rrf_score
                    merged[key]["retrieval_sources"] = [item.get("source", "unknown")]
                else:
                    merged[key]["score"] += rrf_score
                    source = item.get("source", "unknown")
                    sources = merged[key].setdefault("retrieval_sources", [])
                    if source not in sources:
                        sources.append(source)

        return sorted(merged.values(), key=lambda x: x["score"], reverse=True)
