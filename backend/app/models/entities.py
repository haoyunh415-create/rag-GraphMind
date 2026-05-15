from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Document:
    id: str
    filename: str
    content_type: str
    size_bytes: int
    status: str  # pending, chunking, indexing, ready, error
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Chunk:
    id: str
    document_id: str
    text: str
    embedding: list[float] | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Entity:
    id: str
    name: str
    type: str  # PERSON, ORG, PRODUCT, etc.
    properties: dict = field(default_factory=dict)
    source_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class Relation:
    id: str
    source_entity_id: str
    target_entity_id: str
    type: str  # WORKS_FOR, OWNS, SUPPLIES, etc.
    properties: dict = field(default_factory=dict)
