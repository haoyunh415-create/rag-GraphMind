from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Graph RAG Platform"
    debug: bool = False
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001"

    # LLM
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

    # SQLite (local vector store)
    sqlite_db_path: str = "data/rag.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Chunking
    chunk_size: int = 512
    chunk_overlap: int = 64
    max_upload_bytes: int = 10 * 1024 * 1024

    # Retrieval
    top_k: int = 10
    rerank_top_k: int = 5


@lru_cache()
def get_settings() -> Settings:
    return Settings()
