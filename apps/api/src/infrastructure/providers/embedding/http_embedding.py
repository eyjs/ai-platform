"""HTTP 임베딩 프로바이더 (GPU 서버용).

임베딩은 degrade 불가능한 필수 의존점이다(임베딩 없으면 벡터검색=RAG 붕괴).
그래서 일시적 blip 은 재시도로 복구하고, 서버가 죽으면 서킷을 열어 fast-fail 한다.
"""

import asyncio
from typing import List

import httpx

from ..base import EmbeddingProvider
from .._resilience import CircuitBreaker, retry_async


class HttpEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        base_url: str,
        max_concurrent: int = 20,
        dimension: int = 1024,
        timeout: float = 15.0,
        connect_timeout: float = 5.0,
        retry_attempts: int = 3,
        circuit_fail_threshold: int = 5,
        circuit_cooldown_seconds: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            limits=httpx.Limits(
                max_connections=max_concurrent,
                max_keepalive_connections=max_concurrent,
            ),
        )
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._dimension = dimension
        self._retry_attempts = retry_attempts
        self._breaker = CircuitBreaker(
            fail_threshold=circuit_fail_threshold,
            cooldown_seconds=circuit_cooldown_seconds,
            name=f"embedding:{self._base_url}",
        )

    async def _post_embed(self, inputs: List[str]) -> list:
        """/embed 호출 1회 — 재시도/서킷은 호출부(retry_async)가 감싼다."""
        async with self._semaphore:
            response = await self._client.post(
                f"{self._base_url}/embed",
                json={"inputs": inputs},
            )
            response.raise_for_status()
            data = response.json()
        # {"embeddings": [[...floats...]]} 또는 [[...floats...]]
        if isinstance(data, dict) and "embeddings" in data:
            return data["embeddings"]
        if isinstance(data, list):
            return data
        raise ValueError(f"Unexpected embedding response: {type(data)}")

    async def embed(self, text: str) -> List[float]:
        embeddings = await retry_async(
            lambda: self._post_embed([text]),
            attempts=self._retry_attempts,
            breaker=self._breaker,
            name="embedding",
        )
        return embeddings[0]

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return await retry_async(
            lambda: self._post_embed(texts),
            attempts=self._retry_attempts,
            breaker=self._breaker,
            name="embedding",
        )

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def dimension(self) -> int:
        return self._dimension
