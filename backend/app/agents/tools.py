import asyncio
import json
import re
from typing import AsyncGenerator

from loguru import logger

from app.core import cache
from app.core.config import get_settings
from app.core.llm_client import get_llm_client


def _normalize_answer(text: str) -> str:
    """Keep answer markdown readable while trimming noisy whitespace."""
    text = re.sub(r"\s*\[[0-9]+\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _local_grounded_answer(query: str, contexts: list[dict], error: Exception) -> str:
    """Build a deterministic grounded answer when the LLM is unavailable."""
    excerpts: list[str] = []
    for ctx in contexts[:3]:
        doc = ctx.get("document_name", ctx.get("document_id", "未知文档"))
        text = str(ctx.get("text", "")).strip().replace("\n", " ")
        if len(text) > 420:
            text = text[:420].rstrip() + "..."
        if text:
            excerpts.append(f"- {doc}: {text}")

    return "\n\n".join([
        "### 直接回答",
        "模型生成服务暂时不可用，下面是从已检索文档中整理出的可用依据。请检查 LLM 配置后重试，以获得完整回答。",
        "### 依据",
        "\n".join(excerpts) if excerpts else "- 未找到可展示的文档片段。",
        "### 系统提示",
        f"LLM 调用失败：{error}",
    ])


DECOMPOSE_SYSTEM = """You are a query decomposition agent. Break complex questions into simpler, atomic sub-questions that can be answered by searching a knowledge base.

Rules:
- If the question is already simple, return it as-is as a one-item array.
- Split comparisons, multiple entities, time periods, or multi-part questions.
- Each sub-question must be self-contained and specific.
- Output ONLY a JSON array of strings.
"""

SYNTHESIZE_SYSTEM = """You are a precise research analyst. Answer using ONLY the provided document excerpts.

Use a clean Markdown structure and match the user's language.

Required format:
### 直接回答
- Give the direct answer first. Keep it concise.
- Do not add citation markers in the answer text.

### 依据
- List the key supporting facts from the retrieved excerpts.
- Put one fact per bullet.

### 局限
- Include this section only when the excerpts are incomplete, conflicting, or need a caveat.
- If the answer cannot be found in the excerpts, say so clearly and do not guess.

Rules:
1. Only use information from provided contexts.
2. Do not invent facts, numbers, dates, or document names.
3. Use Markdown headings and bullets exactly as above.
4. Never output citation markers such as [1] or [2] in the answer body.
5. Keep the answer compact; avoid generic preambles and closing remarks.
"""

CLASSIFY_SYSTEM = """You are a query classifier. Determine if the user's message needs to search a knowledge base for specific documents/data, or is just general conversation.

A message is "kb" when it asks about specific facts, reports, documents, uploaded files, metrics, dates, summaries, comparisons, or analysis that should be grounded in documents.

A message is "chat" when it is a greeting, general conversation, capability question, brainstorming, or advice that does not require private documents.

Output ONLY one word: "kb" or "chat".
"""

CHAT_SYSTEM = """You are a helpful AI assistant in general chat mode.

Guidelines:
- Be friendly and concise.
- If the user asks about their documents or data, suggest using knowledge-base mode or uploading relevant files.
- Use markdown only when it improves readability.
"""

KB_FALLBACK_NOTE = "\n\n> 注：知识库中没有找到匹配文档，以上回答基于通用知识。"


async def decompose_query(query: str) -> list[str]:
    settings = get_settings()
    client = get_llm_client()

    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": DECOMPOSE_SYSTEM},
                    {"role": "user", "content": query},
                ],
                temperature=0.1,
                max_tokens=300,
            ),
            timeout=8.0,
        )
        raw = (resp.choices[0].message.content or "[]").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("\n", 1)[0]
        sub_queries = json.loads(raw)
        if not isinstance(sub_queries, list) or not sub_queries:
            return [query]
        return [str(item) for item in sub_queries if str(item).strip()] or [query]
    except Exception as e:
        logger.warning(f"Query decomposition failed, using original query: {e}")
        return [query]


async def synthesize_answer(
    query: str,
    contexts: list[dict],
    sub_queries: list[str],
    history: list[dict[str, str]] | None = None,
) -> AsyncGenerator[str, None]:
    settings = get_settings()
    client = get_llm_client()

    if not contexts:
        yield "知识库中没有找到相关文档。请先上传资料，或换一个更具体的问题。"
        return

    cached = await cache.get_cached_answer(query, contexts)
    if cached:
        yield _normalize_answer(cached)
        return

    context_parts: list[str] = []
    for i, ctx in enumerate(contexts, start=1):
        source = ctx.get("source", "unknown")
        doc = ctx.get("document_name", ctx.get("document_id", "unknown"))
        text = ctx.get("text", "")
        chunk = ctx.get("chunk_index")
        chunk_label = f", chunk {chunk}" if chunk is not None else ""
        context_parts.append(f"[{i}] source={source}, document={doc}{chunk_label}\n{text}")
    context_block = "\n\n".join(context_parts)

    sub_query_block = "\n".join(f"- {sq}" for sq in sub_queries) if len(sub_queries) > 1 else sub_queries[0]

    history_block = ""
    if history:
        history_parts = [f"Q: {h['question']}\nA: {h['answer']}" for h in history[-6:]]
        history_block = "Conversation history:\n" + "\n".join(history_parts) + "\n\n"

    user_prompt = f"""Original question: {query}

{history_block}Sub-queries explored:
{sub_query_block}

Retrieved contexts:
{context_block}

Answer the question based on the contexts above."""

    try:
        stream = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYNTHESIZE_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
            stream=True,
        )

        raw_buffer: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                raw_buffer.append(delta.content)
                await asyncio.sleep(0)

        cleaned = _normalize_answer("".join(raw_buffer))
        for line in cleaned.split("\n"):
            yield line.rstrip() + "\n"
            await asyncio.sleep(0)

        asyncio.create_task(cache.set_cached_answer(query, contexts, cleaned))
    except Exception as e:
        logger.error(f"LLM synthesis failed: {e}")
        yield _local_grounded_answer(query, contexts, e)


async def classify_intent(query: str) -> str:
    q_lower = query.lower().strip()
    chat_patterns = (
        "hello", "hi", "hey", "thanks", "thank you", "bye",
        "who are you", "what can you do", "你好", "谢谢", "你是谁", "你能做什么",
    )
    if any(q_lower == p or q_lower.startswith(f"{p} ") for p in chat_patterns):
        return "chat"

    kb_keywords = (
        "knowledge base", "document", "documents", "source", "citation", "retrieve", "retrieval", "rag",
        "知识库", "文档", "资料", "引用", "来源", "检索", "切片", "上传的", "报告", "根据",
        "bm25", "vector", "graph",
    )
    if any(keyword in q_lower for keyword in kb_keywords):
        return "kb"

    if len(q_lower.split()) <= 1 and len(q_lower) < 10:
        return "chat"

    settings = get_settings()
    client = get_llm_client()
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": CLASSIFY_SYSTEM},
                    {"role": "user", "content": query},
                ],
                temperature=0.0,
                max_tokens=3,
            ),
            timeout=4.0,
        )
        result = (resp.choices[0].message.content or "").lower().strip()
        return result if result in ("kb", "chat") else "kb"
    except Exception:
        return "kb"


async def direct_chat(
    query: str,
    history: list[dict[str, str]] | None = None,
    with_fallback_note: bool = False,
) -> AsyncGenerator[str, None]:
    settings = get_settings()
    client = get_llm_client()

    messages: list[dict] = [{"role": "system", "content": CHAT_SYSTEM}]
    if history:
        for h in history[-6:]:
            messages.append({"role": "user", "content": h["question"]})
            messages.append({"role": "assistant", "content": h["answer"]})
    messages.append({"role": "user", "content": query})

    try:
        stream = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=0.7,
            max_tokens=1500,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content
                await asyncio.sleep(0)

        if with_fallback_note:
            yield KB_FALLBACK_NOTE
    except Exception as e:
        logger.error(f"Direct chat failed: {e}")
        yield f"[Error: {e}]"
