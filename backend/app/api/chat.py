import json
import uuid
import asyncio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from app.core.security import require_api_auth
from app.models.schemas import ChatRequest
from app.agents.orchestrator import AgentOrchestrator

router = APIRouter(prefix="/api/chat", tags=["对话"], dependencies=[Depends(require_api_auth)])


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """以 SSE 流式返回回答、引用、状态和检索追踪事件。"""
    query_id = str(uuid.uuid4())[:8]
    orchestrator = AgentOrchestrator(
        query_id=query_id,
        conversation_id=request.conversation_id,
        top_k=request.top_k,
        mode=request.mode,
    )

    async def event_stream():
        async for event in orchestrator.run(request.query):
            # 把事件类型放入 JSON，便于前端统一解析。
            event_type = event["type"]
            payload = json.dumps({"type": event_type, "data": event.get("data", "")}, ensure_ascii=False)
            yield f"event: {event_type}\ndata: {payload}\n\n"
            await asyncio.sleep(0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Query-Id": query_id,
        },
    )
