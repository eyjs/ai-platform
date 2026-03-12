"""HTTP 임베딩 프로바이더 (GPU 서버용)."""

import asyncio
from typing import List

import httpx

from ..base import EmbeddingProvider


class HttpEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        base_url: str,
        max_concurrent: int = 20,
        dimension: int = 1024,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._dimension = dimension

    async def embed(self, text: str) -> List[float]:
        async with self._semaphore:
            response = await self._client.post(
                f"{self._base_url}/embed",
                json={"inputs": [text]},
            )
            response.raise_for_status()
            data = response.json()
            # {"embeddings": [[...floats...]]} 또는 [[...floats...]]
            if isinstance(data, dict) and "embeddings" in data:
                return data["embeddings"][0]
            if isinstance(data, list):
                return data[0]
            raise ValueError(f"Unexpected embedding response: {type(data)}")

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        async with self._semaphore:
            response = await self._client.post(
                f"{self._base_url}/embed",
                json={"inputs": texts},
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and "embeddings" in data:
                return data["embeddings"]
            if isinstance(data, list):
                return data
            raise ValueError(f"Unexpected embedding response: {type(data)}")

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def dimension(self) -> int:
        return self._dimension
