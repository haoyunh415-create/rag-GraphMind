import json
import uuid
import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.models.schemas import ChatRequest
from app.agents.orchestrator import AgentOrchestrator

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("/stream")
async def chat_stream(request: ChatRequest):
    """Stream the agent response as SSE: chunk, citation, status, trace events."""
    query_id = str(uuid.uuid4())[:8]
    orchestrator = AgentOrchestrator(
        query_id=query_id,
        conversation_id=request.conversation_id,
        top_k=request.top_k,
        mode=request.mode,
    )

    async def event_stream():
        async for event in orchestrator.run(request.query):
            # Embed type into the JSON payload so the frontend can read it
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
