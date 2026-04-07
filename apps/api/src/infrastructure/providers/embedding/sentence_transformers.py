"""로컬 Sentence Transformers 임베딩 프로바이더."""

import asyncio
from typing import List

from ..base import EmbeddingProvider


class SentenceTransformersProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "dragonkue/BGE-m3-ko"):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self._dimension = self._model.get_sentence_embedding_dimension()

    async def embed(self, text: str) -> List[float]:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._model.encode, text)
        return result.tolist()

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, self._model.encode, texts)
        return [r.tolist() for r in results]

    @property
    def dimension(self) -> int:
        return self._dimension
