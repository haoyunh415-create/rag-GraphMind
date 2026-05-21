from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "知识图谱 RAG 平台"
    debug: bool = False
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001"
    api_auth_token: str = ""

    # 大模型
    openai_api_key: str = ""
    openai_model: str = "deepseek-chat"
    embedding_model: str = "text-embedding-3-small"
    llm_base_url: str = "https://api.openai.com/v1"

    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "documents"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"

    # Elasticsearch
    es_host: str = "http://localhost:9200"
    es_index: str = "documents"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "documents"

    # SQLite 本地向量检索兜底
    sqlite_db_path: str = "data/rag.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    ingestion_queue_mode: str = "auto"
    ingestion_queue_name: str = "rag:ingestion:queue"
    ingestion_dlq_name: str = ""
    ingestion_queue_dir: str = "data/ingestion-uploads"
    ingestion_max_attempts: int = 3
    ingestion_retry_delay_seconds: float = 2.0

    # 文档切片
    chunk_size: int = 512
    chunk_overlap: int = 64
    max_upload_bytes: int = 10 * 1024 * 1024

    # 检索
    top_k: int = 10
    rerank_top_k: int = 5
    reranker_enabled: bool = True
    reranker_original_weight: float = 0.35
    reranker_query_weight: float = 0.45
    reranker_phrase_weight: float = 0.15
    reranker_source_weight: float = 0.05
    citation_max_items: int = 3
    citation_per_document_limit: int = 2
    citation_min_relative_score: float = 0.55
    citation_min_query_coverage: float = 0.35
    graph_entity_extraction_enabled: bool = True
    graph_entity_extraction_sync: bool = True
    graph_entity_extraction_timeout_seconds: float = 20.0
    retrieval_health_cache_seconds: float = 10.0
    trace_step_budgets_ms: str = "backend_health=500,retrieve=300,rank=100,cite=50,evaluate=100"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
