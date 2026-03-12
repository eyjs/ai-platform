"""LLM 기반 리랭커 (폴백용)."""

import logging
from typing import List

from ..base import LLMProvider, RerankerProvider

logger = logging.getLogger(__name__)


class LLMReranker(RerankerProvider):
    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def rerank(
        self, query: str, documents: List[str], top_k: int = 10
    ) -> List[dict]:
        results = []
        for i, doc in enumerate(documents[:top_k]):
            try:
                response = await self._llm.generate_json(
                    f"질문: {query}\n\n문서: {doc[:500]}\n\n"
                    f'이 문서가 질문에 얼마나 관련 있는지 0~1 점수를 매기세요. '
                    f'JSON으로 {{"score": 0.0~1.0}} 형식으로 답하세요.',
                )
                score = float(response.get("score", 0.0))
            except Exception:
                score = 0.0
            results.append({"index": i, "score": score})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results
