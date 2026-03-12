"""로컬 CrossEncoder 리랭커."""

import asyncio
import logging
import math
from typing import List

from ..base import RerankerProvider

logger = logging.getLogger(__name__)


class CrossEncoderReranker(RerankerProvider):
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name)

    async def rerank(
        self, query: str, documents: List[str], top_k: int = 10
    ) -> List[dict]:
        loop = asyncio.get_event_loop()
        pairs = [[query, doc] for doc in documents]
        raw_scores = await loop.run_in_executor(None, self._model.predict, pairs)

        results = []
        for i, score in enumerate(raw_scores):
            # sigmoid 정규화
            normalized = 1 / (1 + math.exp(-float(score)))
            results.append({"index": i, "score": normalized})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
