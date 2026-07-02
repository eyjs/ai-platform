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
        # 로컬 폴백(CrossEncoder)이 이 환경에서 사용 불가(sentence_transformers 미설치 등)로
        # 판명되면 True. 재시도마다 import 를 반복 시도하지 않도록 1회만 표시한다.
        self._fallback_unavailable = False

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
            # 로컬 CrossEncoder 폴백 시도. 사용 불가(미설치)면 크래시 대신 원순서 유지로 degrade.
            # 리랭킹은 품질 향상 단계이지 정합성 필수가 아니므로, 검색 결과를 통째로 잃는 것보다
            # 순위만 원본대로 두고 파이프라인을 계속하는 편이 낫다.
            if (
                self._fallback is None
                and self._fallback_model
                and not self._fallback_unavailable
            ):
                try:
                    from .cross_encoder import CrossEncoderReranker

                    self._fallback = CrossEncoderReranker(model_name=self._fallback_model)
                except Exception as fe:
                    self._fallback_unavailable = True
                    logger.warning(
                        "local reranker fallback unavailable (%s) — degrading to identity order",
                        fe,
                    )
            if self._fallback:
                try:
                    return await self._fallback.rerank(query, documents, top_k)
                except Exception as re:
                    logger.warning(
                        "local reranker failed (%s) — degrading to identity order", re
                    )
            # 최종 degrade: 입력 순서를 보존한 항등 랭킹(점수 내림차순으로 안정 정렬 보장).
            return self._identity_ranking(documents, top_k)

    @staticmethod
    def _identity_ranking(documents: List[str], top_k: int) -> List[dict]:
        n = min(len(documents), top_k)
        # score 를 index 기준 단조 감소로 부여해 소비자의 score 정렬 후에도 원순서가 유지된다.
        return [{"index": i, "score": 1.0 - (i / max(len(documents), 1))} for i in range(n)]
