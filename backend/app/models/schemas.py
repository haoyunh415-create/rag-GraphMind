from pydantic import BaseModel, Field
from typing import Any, Literal, Optional
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
    job_id: Optional[str] = None
    index_statuses: dict[str, str] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    lifecycle_status: Literal["enabled", "disabled", "test", "archived"] = "enabled"


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
    job_id: Optional[str] = None
    attempt_count: int = 0
    max_attempts: int = 0
    last_error: str = ""
    updated_at: str = ""
    lifecycle_status: Literal["enabled", "disabled", "test", "archived"] = "enabled"
    is_retrievable: bool = True
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


class DocumentStatusUpdateRequest(BaseModel):
    lifecycle_status: Literal["enabled", "disabled", "test", "archived"]


class DocumentStatusUpdateResponse(BaseModel):
    document_id: str
    lifecycle_status: Literal["enabled", "disabled", "test", "archived"]
    is_retrievable: bool


class EvaluationContext(BaseModel):
    source: str = "vector"
    document_id: str = ""
    document_name: str = ""
    chunk_id: Optional[str] = None
    chunk_index: Optional[int] = None
    text: str
    score: float = 0.0
    page: Optional[int] = None


class EvaluationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    answer: Optional[str] = None
    contexts: list[EvaluationContext] = Field(default_factory=list)
    expected_answer: Optional[str] = None
    top_k: int = Field(default=10, ge=1, le=20)


class EvaluationResult(BaseModel):
    evaluation_id: Optional[str] = None
    query_id: Optional[str] = None
    conversation_id: Optional[str] = None
    query: str
    answer: str
    overall_score: float = 0.0
    label: Literal["pass", "warn", "fail"] = "fail"
    groundedness: float = 0.0
    answer_relevance: float = 0.0
    citation_coverage: float = 0.0
    retrieval_quality: float = 0.0
    faithfulness: float
    answer_relevancy: float
    context_recall: float
    context_precision: float
    latency_ms: float
    context_count: int = 0
    citation_count: int = 0
    issues: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class EvaluationListResponse(BaseModel):
    evaluations: list[EvaluationResult] = Field(default_factory=list)
