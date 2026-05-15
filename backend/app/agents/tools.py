import json
import asyncio
import re
from typing import AsyncGenerator

from loguru import logger

from app.core.config import get_settings
from app.core.llm_client import get_llm_client
from app.core import cache


def _normalize_answer(text: str) -> str:
    """Keep answer markdown readable while trimming noisy whitespace."""
    text = re.sub(r"\s*\[[0-9]+\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _local_grounded_answer(query: str, contexts: list[dict], error: Exception) -> str:
    """Build a deterministic answer when the LLM is unavailable."""
    excerpts: list[str] = []
    for ctx in contexts[:3]:
        doc = ctx.get("document_name", ctx.get("document_id", "未知文档"))
        text = str(ctx.get("text", "")).strip().replace("\n", " ")
        if len(text) > 420:
            text = text[:420].rstrip() + "..."
        if text:
            excerpts.append(f"- {doc}: {text}")

    lowered = query.lower()
    direct_answer = ""
    if "前端" in query and ("端口" in query or "port" in lowered):
        joined = " ".join(str(ctx.get("text", "")) for ctx in contexts[:5])
        match = re.search(r"前端(?:服务)?(?:默认)?(?:访问地址是\s*`?http://localhost:)?(?:运行在\s*)?(\d{4,5})\s*端口", joined)
        if match:
            direct_answer = f"前端默认运行在 {match.group(1)} 端口。"

    if not direct_answer:
        direct_answer = "大模型暂时不可用，但知识库已经检索到相关片段。请根据下面依据核对答案。"

    return "\n\n".join([
        "### 结论",
        direct_answer,
        "### 依据",
        "\n".join(excerpts) if excerpts else "- 未找到可展示的文本片段。",
        "### 补充说明",
        f"生成阶段调用大模型失败，请检查 OPENAI_API_KEY / LLM_BASE_URL / OPENAI_MODEL。错误摘要：{error}",
    ])

DECOMPOSE_SYSTEM = """You are a query decomposition agent. Your job is to break complex questions into simpler, atomic sub-questions that can be answered by searching a knowledge base.

Rules:
- If the question is already simple and can be answered by a single search, return it as-is (one item).
- If the question requires comparing multiple entities, time periods, or concepts, split into separate sub-questions.
- Each sub-question should be self-contained and specific.
- Output ONLY a JSON array of strings, nothing else.

Examples:
Q: "What were our Q3 and Q4 revenue numbers, and which grew faster?"
A: ["What was the revenue in Q3?", "What was the revenue in Q4?"]

Q: "Summarize the security incident from last week"
A: ["Summarize the security incident from last week"]

Q: "Compare the API performance of service A vs service B, and tell me which has more issues"
A: ["What is the API performance of service A?", "What is the API performance of service B?", "What issues does service A have?", "What issues does service B have?"]
"""

SYNTHESIZE_SYSTEM = """You are a precise research analyst. Answer using ONLY the provided document excerpts.

CRITICAL: DO NOT use ANY markdown formatting. No **bold**, no ## headers, no - bullets, no `code`, no | tables|, no __underline__, no *italic*. Just plain text.

Output format:
- One fact per line
- Blank line between different facts
- Start directly with the first fact
- No opening summary, no closing notes, no disclaimers
- [N] citation at end of line
- Match the user's language

Example of CORRECT output:

Python 是编程语言，用于机器学习 [1]

PyTorch 是深度学习框架，基于 Python [1]

TensorFlow 是深度学习框架，由 Google 开发 [1]

Example of WRONG output (do NOT do this):

**实体关系**
- Python 是编程语言 [1]
- PyTorch 是深度学习框架 [1]

RULES:
1. Only use information from provided contexts
2. One fact per line, blank line between lines
3. No markdown formatting at all
4. No preamble, no closing remarks"""

SYNTHESIZE_SYSTEM = """You are a precise research analyst. Answer using ONLY the provided document excerpts.

Use a clean Markdown structure and match the user's language.

Required format:
### 结论
- Give the direct answer first. Keep it concise.
- Do not add citation markers in the answer text.

### 依据
- List the key supporting facts from the retrieved excerpts.
- Put one fact per bullet.

### 补充说明
- Include this section only when the excerpts are incomplete, conflicting, or need a caveat.
- If the answer cannot be found in the excerpts, say so clearly and do not guess.

Rules:
1. Only use information from provided contexts.
2. Do not invent facts, numbers, dates, or document names.
3. Use Markdown headings and bullets exactly as above.
4. Never output citation markers such as [1], [2], or source suffixes in the answer body.
5. Keep the answer compact; avoid generic preambles and closing remarks."""


async def decompose_query(query: str) -> list[str]:
    """Decompose a complex query into simpler sub-queries using the LLM."""
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
        raw = resp.choices[0].message.content or "[]"
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("\n", 1)[0]
        sub_queries: list[str] = json.loads(raw)
        if not isinstance(sub_queries, list) or len(sub_queries) == 0:
            return [query]
        logger.info(f"Decomposed '{query[:60]}...' into {len(sub_queries)} sub-queries: {sub_queries}")
        return sub_queries
    except Exception as e:
        logger.warning(f"Query decomposition failed, using original query: {e}")
        return [query]


async def synthesize_answer(
    query: str,
    contexts: list[dict],
    sub_queries: list[str],
    history: list[dict[str, str]] | None = None,
) -> AsyncGenerator[str, None]:
    """Stream the synthesized answer from retrieved contexts with citations."""
    settings = get_settings()
    client = get_llm_client()

    if not contexts:
        yield "No relevant documents found in the knowledge base. Try uploading some documents first."
        return

    # --- Check cache first ---
    cached = await cache.get_cached_answer(query, contexts)
    if cached:
        yield _normalize_answer(cached)
        return

    # Build numbered context block
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
        history_parts = []
        for h in history[-6:]:  # last 6 turns
            history_parts.append(f"Q: {h['question']}\nA: {h['answer']}")
        history_block = "Conversation history:\n" + "\n".join(history_parts) + "\n\n"

    user_prompt = f"""Original question: {query}

{history_block}Sub-queries explored:
{sub_query_block}

Retrieved contexts:
{context_block}

Answer the question based on the contexts above.
Use the required Markdown format. Do not include citation markers such as [1] or [2] in the answer body."""

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

        full_answer = "".join(raw_buffer)
        cleaned = _normalize_answer(full_answer)
        # Yield normalized text line by line.
        for line in cleaned.split("\n"):
            yield line.rstrip() + "\n"
            await asyncio.sleep(0)

        asyncio.create_task(cache.set_cached_answer(query, contexts, cleaned))

    except Exception as e:
        logger.error(f"LLM synthesis failed: {e}")
        yield _local_grounded_answer(query, contexts, e)


# ------------------------------------------------------------------
# Intent classifier & direct chat
# ------------------------------------------------------------------

CLASSIFY_SYSTEM = """You are a query classifier. Determine if the user's message needs to search a knowledge base for specific documents/data, or is just general conversation.

A message is "kb" (knowledge base) ONLY when:
- It asks about specific facts, data, reports, documents
- It mentions entities like company names, product names, metrics, dates
- It asks to summarize, compare, or analyze information that would be in documents

A message is "chat" when:
- Greetings, chit-chat ("hello", "how are you")
- Questions about yourself, your capabilities ("what can you do", "who are you")
- General advice, brainstorming, opinions
- Simple follow-ups to previous conversation

Output ONLY one word: "kb" or "chat"."""

CHAT_SYSTEM = """You are a helpful AI assistant. You have access to a knowledge base but are currently in general chat mode.

Guidelines:
- Be friendly and concise
- Answer questions thoughtfully, drawing on your general knowledge
- If the user asks about their documents or data, suggest they upload relevant files
- Use markdown formatting when it improves readability"""

KB_FALLBACK_NOTE = "\n\n> *Note: no matching documents were found in your knowledge base. The answer above is based on general knowledge.*"


async def classify_intent(query: str) -> str:
    """Quickly classify whether this is a knowledge-base query or general chat."""
    # Fast keyword pre-filter: obvious chat patterns
    chat_patterns = (
        "hello", "hi ", "hey", "good morning", "good afternoon",
        "how are you", "what's up", "thanks", "thank you", "bye",
        "who are you", "what can you do", "what do you do",
        "测试", "你好", "早", "谢谢", "再见",
    )
    q_lower = query.lower().strip()
    if any(q_lower.startswith(p) for p in chat_patterns):
        return "chat"

    kb_keywords = (
        "knowledge base", "document", "documents", "source", "sources",
        "citation", "citations", "retrieve", "retrieval", "rag",
        "知识库", "文档", "资料", "来源", "引用", "检索", "片段", "索引",
        "回答策略", "混合检索", "向量", "bm25", "图谱",
    )
    if any(keyword in q_lower for keyword in kb_keywords):
        return "kb"

    # Short single-word queries are likely chat
    if len(q_lower.split()) <= 1 and len(q_lower) < 10:
        return "chat"

    # LLM classification for ambiguous cases
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
        # On timeout/error, default to kb (safer: will find docs or gracefully fallback)
        return "kb"


async def direct_chat(
    query: str,
    history: list[dict[str, str]] | None = None,
    with_fallback_note: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream a general chat response without retrieval."""
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
