import uuid
from app.core.config import get_settings


class Chunker:
    """Split documents into overlapping chunks with metadata."""

    def __init__(self):
        self.settings = get_settings()

    def chunk(self, text: str, document_id: str) -> list[dict]:
        size = self.settings.chunk_size
        overlap = self.settings.chunk_overlap
        step = size - overlap

        words = text.split()
        chunks = []
        for i in range(0, len(words), step):
            chunk_text = " ".join(words[i : i + size])
            if len(chunk_text) < 50:
                continue
            chunks.append(
                {
                    "id": str(uuid.uuid4()),
                    "document_id": document_id,
                    "text": chunk_text,
                    "chunk_index": len(chunks),
                    "char_start": i,
                    "char_end": i + len(chunk_text),
                }
            )
        return chunks
