"""OpenAI 임베딩 프로바이더."""

from typing import List

from ..base import EmbeddingProvider

# text-embedding-3-small: 1536차원
_OPENAI_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model
        self._dimension = _OPENAI_DIMENSIONS.get(model, 1536)

    async def embed(self, text: str) -> List[float]:
        response = await self._client.embeddings.create(input=text, model=self._model)
        return response.data[0].embedding

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = await self._client.embeddings.create(input=texts, model=self._model)
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]

    @property
    def dimension(self) -> int:
        return self._dimension
