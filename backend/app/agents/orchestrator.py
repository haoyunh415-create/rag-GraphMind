import asyncio
from typing import AsyncGenerator

from loguru import logger

from app.agents.tools import classify_intent, decompose_query, direct_chat, synthesize_answer
from app.core import conversation
from app.core.config import get_settings
from app.core.observability import RetrievalTrace
from app.retrieval.bm25_search import BM25Search
from app.retrieval.fusion import FusionRanker
from app.retrieval.health import retrieval_health
from app.retrieval.knowledge_graph import KnowledgeGraph
from app.retrieval.vector_store import VectorStore


class AgentOrchestrator:
    """Orchestrate routing, retrieval, fusion, answer generation, and trace events."""

    def __init__(
        self,
        query_id: str,
        conversation_id: str | None = None,
        top_k: int | None = None,
        mode: str = "auto",
    ):
        self.query_id = query_id
        self.conversation_id = conversation_id
        self.settings = get_settings()
        self.top_k = max(1, min(top_k or self.settings.top_k, 20))
        self.mode = mode if mode in ("auto", "kb", "chat") else "auto"
        self.vector_store = VectorStore()
        self.graph = KnowledgeGraph()
        self.bm25 = BM25Search()
        self.fusion = FusionRanker()

    async def run(self, query: str) -> AsyncGenerator[dict, None]:
        trace = RetrievalTrace(query_id=self.query_id, original_query=query)

        history: list[dict[str, str]] = []
        if self.conversation_id:
            history = await conversation.get_history(self.conversation_id)
            if history:
                trace.add_step("history", {"turns": len(history)})

        yield {"type": "status", "data": {"status": "routing", "detail": "Classifying query intent"}}
        await asyncio.sleep(0)

        if self.mode == "kb":
            intent = "kb"
        elif self.mode == "chat":
            intent = "chat"
        else:
            intent = await classify_intent(query)
        trace.add_step("intent", {"intent": intent, "mode": self.mode})

        if intent == "chat":
            yield {"type": "status", "data": {"status": "chat", "detail": "Using general chat mode"}}
            await asyncio.sleep(0)
            async for event in self._stream_direct_chat(query, history, trace, "chat"):
                yield event
            return

        try:
            chunk_count = await asyncio.wait_for(self.vector_store.count(), timeout=2.0)
        except Exception as e:
            logger.warning(f"Knowledge base count failed, continuing with retrieval: {e}")
            chunk_count = -1

        if chunk_count == 0:
            if self.mode == "kb":
                message = "当前知识库为空，无法基于文档回答。请先上传可检索文档，或切换到聊天模式。"
                yield {"type": "status", "data": {"status": "blocked", "detail": "Knowledge base is empty"}}
                yield {"type": "citations", "data": []}
                yield {"type": "chunk", "data": message}
                trace.add_step("blocked_empty_kb", {"reason": "empty_knowledge_base", "tokens": 1})
                yield {"type": "trace", "data": trace.to_dict()}
                return

            yield {
                "type": "status",
                "data": {"status": "chat", "detail": "Knowledge base is empty; using general chat"},
            }
            await asyncio.sleep(0)
            async for event in self._stream_direct_chat(query, history, trace, "chat_empty_kb"):
                yield event
            return

        yield {"type": "status", "data": {"status": "decomposing", "detail": "Breaking down query into sub-questions"}}
        await asyncio.sleep(0)

        sub_queries = await decompose_query(query)
        trace.sub_queries = sub_queries
        trace.add_step("decompose", {"sub_queries": sub_queries})

        yield {
            "type": "status",
            "data": {"status": "retrieving", "detail": f"Searching across {len(sub_queries)} sub-queries"},
        }
        await asyncio.sleep(0)

        backend_health = await retrieval_health()
        yield {
            "type": "status",
            "data": {"status": "retrieving", "detail": "Checking retrieval backends", "backends": backend_health},
        }
        await asyncio.sleep(0)
        trace.add_step("backend_health", {"backends": backend_health})

        all_results: list[dict] = []
        retrieval_counts = {"vector": 0, "graph": 0, "bm25": 0}
        retrieval_errors: list[str] = []
        retrieval_details: list[dict] = []

        for sq in sub_queries:
            jobs = {"vector": self.vector_store.search(sq, self.top_k)}
            if backend_health["graph"]["available"]:
                jobs["graph"] = self.graph.search(sq, self.top_k)
            else:
                retrieval_errors.append(f"graph: {backend_health['graph']['detail']}")
            if backend_health["bm25"]["available"]:
                jobs["bm25"] = self.bm25.search(sq, self.top_k)
            else:
                retrieval_errors.append(f"bm25: {backend_health['bm25']['detail']}")

            results_by_source = dict(zip(jobs.keys(), await asyncio.gather(*jobs.values(), return_exceptions=True)))

            subquery_detail = {"query": sq, "sources": {}}
            for source, results in results_by_source.items():
                if isinstance(results, Exception):
                    retrieval_errors.append(f"{source}: {results}")
                    subquery_detail["sources"][source] = {"count": 0, "error": str(results), "results": []}
                    continue
                items = results if isinstance(results, list) else []
                retrieval_counts[source] += len(items)
                for item in items:
                    item["source"] = source
                all_results.extend(items)
                subquery_detail["sources"][source] = {"count": len(items), "results": _summarize_results(items)}
            retrieval_details.append(subquery_detail)

        trace.add_step("retrieve", {"counts": retrieval_counts, "errors": retrieval_errors, "details": retrieval_details})

        yield {
            "type": "status",
            "data": {"status": "ranking", "detail": f"Fusing and re-ranking {len(all_results)} results"},
        }
        await asyncio.sleep(0)

        ranked = await self.fusion.rank(query, all_results, limit=self.top_k)
        trace.add_step("rank", {"input_count": len(all_results), "output_count": len(ranked), "results": _summarize_results(ranked)})

        citations = [r for r in ranked if isinstance(r, dict)]
        yield {"type": "citations", "data": citations}
        await asyncio.sleep(0)

        token_count = 0
        answer_buffer: list[str] = []

        if ranked:
            yield {"type": "status", "data": {"status": "generating", "detail": "Synthesizing answer from documents"}}
            async for token in synthesize_answer(query, ranked, sub_queries, history):
                token_count += 1
                answer_buffer.append(token)
                yield {"type": "chunk", "data": token}
        else:
            if self.mode == "kb":
                message = "当前知识库没有检索到足够依据，无法基于文档回答。你可以换个问法、补充文档，或切换到聊天模式。"
                yield {
                    "type": "status",
                    "data": {"status": "blocked", "detail": "No grounded context found in knowledge base mode"},
                }
                answer_buffer.append(message)
                token_count = 1
                yield {"type": "chunk", "data": message}
                trace.add_step("blocked_no_evidence", {"reason": "no_retrieval_results", "tokens": token_count})
                if self.conversation_id:
                    asyncio.create_task(conversation.append_history(self.conversation_id, query, "".join(answer_buffer)))
                yield {"type": "trace", "data": trace.to_dict()}
                return

            yield {
                "type": "status",
                "data": {"status": "generating", "detail": "No documents found, answering from general knowledge"},
            }
            async for token in direct_chat(query, history, with_fallback_note=True):
                token_count += 1
                answer_buffer.append(token)
                yield {"type": "chunk", "data": token}

        trace.add_step("generate", {"tokens": token_count})

        if self.conversation_id:
            asyncio.create_task(conversation.append_history(self.conversation_id, query, "".join(answer_buffer)))

        trace_dict = trace.to_dict()
        logger.info(
            f"Query '{query[:60]}' completed in {trace_dict['total_ms']:.0f}ms | "
            f"vector={retrieval_counts['vector']} graph={retrieval_counts['graph']} bm25={retrieval_counts['bm25']} | "
            f"ranked={len(ranked)} tokens={token_count}"
        )
        yield {"type": "trace", "data": trace_dict}

    async def _stream_direct_chat(
        self,
        query: str,
        history: list[dict[str, str]],
        trace: RetrievalTrace,
        trace_step: str,
    ) -> AsyncGenerator[dict, None]:
        buffer: list[str] = []
        async for token in direct_chat(query, history):
            buffer.append(token)
            yield {"type": "chunk", "data": token}

        if self.conversation_id:
            asyncio.create_task(conversation.append_history(self.conversation_id, query, "".join(buffer)))

        trace.add_step(trace_step, {"tokens": len(buffer)})
        yield {"type": "trace", "data": trace.to_dict()}


def _summarize_results(results: list[dict], limit: int = 5) -> list[dict]:
    summary: list[dict] = []
    for item in results[:limit]:
        text = str(item.get("text", "")).replace("\n", " ")
        if len(text) > 220:
            text = text[:220] + "..."
        summary.append({
            "id": item.get("id") or item.get("chunk_id") or "",
            "document_id": item.get("document_id", ""),
            "document_name": item.get("document_name", ""),
            "chunk_index": item.get("chunk_index", 0),
            "source": item.get("source", "unknown"),
            "score": float(item.get("score", 0) or 0),
            "text": text,
        })
    return summary
