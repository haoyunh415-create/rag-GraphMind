from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_llm_client() -> AsyncOpenAI:
    """Singleton OpenAI client with global timeout."""
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.llm_base_url,
        timeout=30.0,
        max_retries=1,
    )
