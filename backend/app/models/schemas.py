from pydantic import BaseModel, Field
from typing import Literal, Optional
from enum import Enum


class RetrievalSource(str, Enum):
    VECTOR = "vector"
    GRAPH = "graph"
    BM25 = "bm25"


class Citation(BaseModel):
    source: RetrievalSource
    document_id: str
    document_name: str
    chunk_id: str
    text: str
    score: float
    page: Optional[int] = None


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = None
    top_k: int = Field(default=10, ge=1, le=20)
    mode: Literal["auto", "kb", "chat"] = "auto"


class ChatChunk(BaseModel):
    type: str = "chunk"
    content: str


class CitationChunk(BaseModel):
    type: str = "citation"
    citations: list[Citation]


class TraceChunk(BaseModel):
    type: str = "trace"
    trace: dict


class StatusChunk(BaseModel):
    type: str = "status"
    status: str
    detail: str


class DocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    chunk_count: int
    status: str
    index_statuses: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class KnowledgeBaseStats(BaseModel):
    total_documents: int
    total_chunks: int
    total_entities: int
    total_relations: int
    storage_size_bytes: int


class KnowledgeBaseDocument(BaseModel):
    document_id: str
    document_name: str
    chunk_count: int
    status: str = "ready"
    index_statuses: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class KnowledgeBaseDocumentsResponse(BaseModel):
    documents: list[KnowledgeBaseDocument] = Field(default_factory=list)


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    document_name: str
    chunk_index: int
    text: str


class DocumentChunksResponse(BaseModel):
    document_id: str
    chunks: list[DocumentChunk] = Field(default_factory=list)


class EvaluationRequest(BaseModel):
    query: str
    expected_answer: Optional[str] = None


class EvaluationResult(BaseModel):
    query: str
    answer: str
    faithfulness: float
    answer_relevancy: float
    context_recall: float
    context_precision: float
    latency_ms: float
