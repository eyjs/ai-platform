"""HTTP 리랭커 프로바이더 (HuggingFace TEI API)."""

import logging
import math
from typing import List, Optional

import httpx

from ..base import RerankerProvider

logger = logging.getLogger(__name__)


class HttpRerankerProvider(RerankerProvider):
    def __init__(self, base_url: str, fallback_model: Optional[str] = None):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)
        self._fallback_model = fallback_model
        self._fallback: Optional[RerankerProvider] = None

    async def close(self) -> None:
        await self._client.aclose()

    async def rerank(
        self, query: str, documents: List[str], top_k: int = 10
    ) -> List[dict]:
        try:
            response = await self._client.post(
                f"{self._base_url}/rerank",
                json={"query": query, "texts": documents, "return_text": False},
            )
            response.raise_for_status()
            raw = response.json()
            results = []
            for item in raw:
                score = 1 / (1 + math.exp(-float(item["score"])))
                results.append({"index": item["index"], "score": score})
            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:top_k]
        except Exception as e:
            logger.warning("HTTP reranker failed: %s, falling back to local", e)
            if self._fallback is None and self._fallback_model:
                from .cross_encoder import CrossEncoderReranker

                self._fallback = CrossEncoderReranker(model_name=self._fallback_model)
            if self._fallback:
                return await self._fallback.rerank(query, documents, top_k)
            raise
