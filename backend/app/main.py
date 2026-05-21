from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import chat, documents, knowledge_base
from app.core.config import get_settings

settings = get_settings()
cors_origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(knowledge_base.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
