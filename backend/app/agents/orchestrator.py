import asyncio
from typing import AsyncGenerator

from loguru import logger

from app.agents.tools import decompose_query, synthesize_answer, classify_intent, direct_chat
from app.retrieval.vector_store import VectorStore
from app.retrieval.knowledge_graph import KnowledgeGraph
from app.retrieval.bm25_search import BM25Search
from app.retrieval.citation_pruner import prune_citations_with_report
from app.retrieval.fusion import FusionRanker
from app.retrieval.health import retrieval_health
from app.core.config import get_settings
from app.core.observability import RetrievalTrace
from app.core import conversation
from app.evaluation.rag_quality import evaluate_rag_answer
from app.evaluation.store import EvaluationStore


class AgentOrchestrator:
    """Orchestrates multi-agent RAG: decompose → parallel retrieval → fusion → synthesize."""

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

        # --- Step 0: Load conversation history ---
        history: list[dict[str, str]] = []
        if self.conversation_id:
            history = await conversation.get_history(self.conversation_id)
            if history:
                trace.add_step("history", {"turns": len(history)})

        # --- Step 0.5: Intent routing ---
        yield {"type": "status", "data": {"status": "routing", "detail": "正在判断是否需要检索知识库"}}
        await asyncio.sleep(0)

        if self.mode == "kb":
            intent = "kb"
        elif self.mode == "chat":
            intent = "chat"
        else:
            intent = await classify_intent(query)
        trace.add_step("intent", {"intent": intent, "mode": self.mode})

        if intent == "chat":
            # General chat — skip retrieval, stream directly
            yield {"type": "status", "data": {"status": "chat", "detail": "聊天模式，跳过知识库检索"}}
            await asyncio.sleep(0)

            buffer: list[str] = []
            async for token in direct_chat(query, history):
                buffer.append(token)
                yield {"type": "chunk", "data": token}

            if self.conversation_id:
                asyncio.create_task(conversation.append_history(
                    self.conversation_id, query, "".join(buffer)
                ))

            trace.add_step("chat", {"tokens": len(buffer)})
            evaluation = await self._evaluate_turn(trace, query, "".join(buffer), [])
            yield {"type": "evaluation", "data": evaluation.model_dump()}
            yield {"type": "trace", "data": trace.to_dict()}
            return

        try:
            documents = await asyncio.wait_for(self.vector_store.list_documents(), timeout=2.0)
        except Exception as e:
            logger.warning(f"Knowledge base document listing failed, continuing with retrieval: {e}")
            documents = []

        enabled_document_ids = {
            str(doc.get("document_id") or "")
            for doc in documents
            if doc.get("document_id") and doc.get("is_retrievable", True)
        }
        trace.add_step("document_filter", {
            "total_documents": len(documents),
            "enabled_documents": len(enabled_document_ids),
            "allowed_lifecycle_status": "enabled",
        })

        if not documents or not enabled_document_ids:
            if self.mode == "kb":
                message = "当前知识库没有启用文档，无法基于文档回答。请先上传或启用可检索文档，或切换到聊天模式。"
                yield {"type": "status", "data": {"status": "blocked", "detail": "知识库没有启用文档"}}
                yield {"type": "citations", "data": []}
                yield {"type": "chunk", "data": message}
                trace.add_step("blocked_empty_kb", {"reason": "no_enabled_documents", "tokens": 1})
                evaluation = await self._evaluate_turn(trace, query, message, [])
                yield {"type": "evaluation", "data": evaluation.model_dump()}
                yield {"type": "trace", "data": trace.to_dict()}
                return

            yield {"type": "status", "data": {"status": "chat", "detail": "知识库没有启用文档，改用通用聊天"}}
            await asyncio.sleep(0)

            buffer: list[str] = []
            async for token in direct_chat(query, history):
                buffer.append(token)
                yield {"type": "chunk", "data": token}

            if self.conversation_id:
                asyncio.create_task(conversation.append_history(
                    self.conversation_id, query, "".join(buffer)
                ))

            trace.add_step("chat_empty_kb", {"tokens": len(buffer)})
            evaluation = await self._evaluate_turn(trace, query, "".join(buffer), [])
            yield {"type": "evaluation", "data": evaluation.model_dump()}
            yield {"type": "trace", "data": trace.to_dict()}
            return

        # --- Step 1: Decompose ---
        yield {"type": "status", "data": {"status": "decomposing", "detail": "正在拆解问题"}}
        await asyncio.sleep(0)

        sub_queries = await decompose_query(query)
        trace.sub_queries = sub_queries
        trace.add_step("decompose", {"sub_queries": sub_queries})

        # --- Step 2: Parallel retrieval ---
        yield {"type": "status", "data": {"status": "retrieving", "detail": f"正在检索 {len(sub_queries)} 个子问题"}}
        await asyncio.sleep(0)

        backend_health = await retrieval_health()
        yield {"type": "status", "data": {"status": "retrieving", "detail": "正在检查检索后端", "backends": backend_health}}
        await asyncio.sleep(0)
        trace.add_step("backend_health", {"backends": backend_health})

        all_results: list[dict] = []
        retrieval_counts = {"vector": 0, "graph": 0, "bm25": 0}
        filtered_counts = {"vector": 0, "graph": 0, "bm25": 0}
        retrieval_errors: list[str] = []
        retrieval_details: list[dict] = []
        search_k = min(max(self.top_k * 3, self.top_k), 50)

        for sq in sub_queries:
            jobs: dict[str, asyncio.Future] = {
                "vector": self.vector_store.search(sq, search_k),
            }
            if backend_health["graph"]["available"]:
                jobs["graph"] = self.graph.search(sq, search_k)
            else:
                retrieval_errors.append(f"graph: {backend_health['graph']['detail']}")
            if backend_health["bm25"]["available"]:
                jobs["bm25"] = self.bm25.search(sq, search_k)
            else:
                retrieval_errors.append(f"bm25: {backend_health['bm25']['detail']}")

            results_by_source = dict(zip(
                jobs.keys(),
                await asyncio.gather(*jobs.values(), return_exceptions=True),
            ))

            subquery_detail = {"query": sq, "sources": {}}
            for source, results in results_by_source.items():
                if isinstance(results, Exception):
                    retrieval_errors.append(f"{source}: {results}")
                    subquery_detail["sources"][source] = {
                        "count": 0,
                        "error": str(results),
                        "results": [],
                    }
                    continue
                raw_items = results if isinstance(results, list) else []
                items, filtered = _filter_retrievable_results(raw_items, enabled_document_ids)
                retrieval_counts[source] += len(items)
                filtered_counts[source] += filtered
                for r in items:
                    r["source"] = source
                all_results.extend(items)
                subquery_detail["sources"][source] = {
                    "count": len(items),
                    "filtered": filtered,
                    "results": _summarize_results(items),
                }
            retrieval_details.append(subquery_detail)

        trace.add_step("retrieve", {
            "counts": retrieval_counts,
            "filtered_counts": filtered_counts,
            "errors": retrieval_errors,
            "details": retrieval_details,
        })

        # --- Step 3: Fusion + rerank ---
        yield {"type": "status", "data": {"status": "ranking", "detail": f"正在融合并重排 {len(all_results)} 条结果"}}
        await asyncio.sleep(0)

        ranked = await self.fusion.rank(query, all_results, limit=self.top_k)
        trace.add_step("rank", {
            "input_count": len(all_results),
            "output_count": len(ranked),
            "results": _summarize_results(ranked),
            "reranker": {
                "enabled": self.settings.reranker_enabled,
                "weights": {
                    "original": self.settings.reranker_original_weight,
                    "query": self.settings.reranker_query_weight,
                    "phrase": self.settings.reranker_phrase_weight,
                    "source": self.settings.reranker_source_weight,
                },
            },
        })

        # --- Step 4: Evidence pruning + citations ---
        citation_report = prune_citations_with_report(query, ranked)
        citations = citation_report["selected"]
        trace.add_step("cite", {
            "input_count": len(ranked),
            "output_count": len(citations),
            "results": _summarize_results(citations),
            "candidates": _summarize_results(citation_report["candidates"], limit=10),
            "rejection_counts": citation_report["rejection_counts"],
            "settings": citation_report["settings"],
        })
        yield {"type": "citations", "data": citations}
        await asyncio.sleep(0)

        # --- Step 5: Generate ---
        token_count = 0
        answer_buffer: list[str] = []

        if citations:
            yield {"type": "status", "data": {"status": "generating", "detail": "正在基于文档生成回答"}}
            async for token in synthesize_answer(query, citations, sub_queries, history):
                token_count += 1
                answer_buffer.append(token)
                yield {"type": "chunk", "data": token}
        else:
            if self.mode == "kb":
                message = "当前知识库未检索到足够依据，无法基于文档回答。你可以换个问法、补充文档，或切换到聊天模式。"
                yield {"type": "status", "data": {"status": "blocked", "detail": "知识库模式下没有找到足够依据"}}
                token_count = 1
                answer_buffer.append(message)
                yield {"type": "chunk", "data": message}
                trace.add_step("blocked_no_evidence", {"reason": "no_retrieval_results", "tokens": token_count})
                if self.conversation_id:
                    asyncio.create_task(conversation.append_history(
                        self.conversation_id, query, "".join(answer_buffer)
                    ))
                evaluation = await self._evaluate_turn(trace, query, "".join(answer_buffer), [])
                yield {"type": "evaluation", "data": evaluation.model_dump()}
                yield {"type": "trace", "data": trace.to_dict()}
                return

            yield {"type": "status", "data": {"status": "generating", "detail": "未找到文档依据，改用通用知识回答"}}
            async for token in direct_chat(query, history, with_fallback_note=True):
                token_count += 1
                answer_buffer.append(token)
                yield {"type": "chunk", "data": token}

        trace.add_step("generate", {"tokens": token_count})

        evaluation = await self._evaluate_turn(trace, query, "".join(answer_buffer), citations)
        yield {"type": "evaluation", "data": evaluation.model_dump()}

        # --- Save conversation turn ---
        if self.conversation_id:
            asyncio.create_task(conversation.append_history(
                self.conversation_id, query, "".join(answer_buffer)
            ))

        # --- Final: Trace ---
        trace_dict = trace.to_dict()
        logger.info(
            f"Query '{query[:60]}' completed in {trace_dict['total_ms']:.0f}ms | "
            f"vector={retrieval_counts['vector']} graph={retrieval_counts['graph']} bm25={retrieval_counts['bm25']} | "
            f"ranked={len(ranked)} citations={len(citations)} tokens={token_count}"
        )
        yield {"type": "trace", "data": trace_dict}

    async def _evaluate_turn(
        self,
        trace: RetrievalTrace,
        query: str,
        answer: str,
        contexts: list[dict],
    ):
        trace_snapshot = trace.to_dict()
        evaluation = evaluate_rag_answer(
            query=query,
            answer=answer,
            contexts=contexts,
            latency_ms=trace_snapshot["total_ms"],
            query_id=self.query_id,
            conversation_id=self.conversation_id,
        )
        evaluation = await EvaluationStore().save(
            evaluation,
            contexts=contexts,
            trace=trace_snapshot,
        )
        trace.add_step("evaluate", {
            "overall_score": evaluation.overall_score,
            "label": evaluation.label,
            "groundedness": evaluation.groundedness,
            "answer_relevance": evaluation.answer_relevance,
            "citation_coverage": evaluation.citation_coverage,
            "retrieval_quality": evaluation.retrieval_quality,
            "issues": evaluation.issues,
        })
        return evaluation


def _summarize_results(results: list[dict], limit: int = 5) -> list[dict]:
    summary: list[dict] = []
    for item in results[:limit]:
        text = str(item.get("text", "")).replace("\n", " ")
        if len(text) > 220:
            text = text[:220] + "..."
        row = {
            "id": item.get("id") or item.get("chunk_id") or "",
            "document_id": item.get("document_id", ""),
            "document_name": item.get("document_name", ""),
            "chunk_index": item.get("chunk_index", 0),
            "source": item.get("source", "unknown"),
            "score": float(item.get("score", 0) or 0),
            "text": text,
        }
        for key in (
            "_citation_selected",
            "_citation_reason",
            "_citation_rejection_reason",
            "_citation_relative_score",
            "_citation_query_coverage",
            "_rrf_score",
            "_rerank_score",
        ):
            if key in item:
                row[key.removeprefix("_")] = item[key]
        if item.get("retrieval_sources"):
            row["citation_sources"] = item["retrieval_sources"]
        if item.get("_rerank_features"):
            row["rerank_features"] = item["_rerank_features"]
        if item.get("graph_context"):
            row["graph_context"] = item["graph_context"]
        summary.append(row)
    return summary


def _filter_retrievable_results(
    results: list[dict],
    enabled_document_ids: set[str],
) -> tuple[list[dict], int]:
    if not enabled_document_ids:
        return [], len(results)

    filtered: list[dict] = []
    removed = 0
    for item in results:
        document_id = str(item.get("document_id") or "")
        if document_id in enabled_document_ids:
            filtered.append(item)
        else:
            removed += 1
    return filtered, removed
