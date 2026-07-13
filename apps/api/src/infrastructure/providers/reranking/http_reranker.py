"""HTTP 리랭커 프로바이더 (HuggingFace TEI API)."""

import logging
import math
from typing import List, Optional

import httpx

from ..base import RerankerProvider
from .._resilience import CircuitBreaker, retry_async

logger = logging.getLogger(__name__)


class HttpRerankerProvider(RerankerProvider):
    def __init__(
        self,
        base_url: str,
        fallback_model: Optional[str] = None,
        retry_attempts: int = 2,
        circuit_fail_threshold: int = 5,
        circuit_cooldown_seconds: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)
        self._fallback_model = fallback_model
        self._fallback: Optional[RerankerProvider] = None
        # 로컬 폴백(CrossEncoder)이 이 환경에서 사용 불가(sentence_transformers 미설치 등)로
        # 판명되면 True. 재시도마다 import 를 반복 시도하지 않도록 1회만 표시한다.
        self._fallback_unavailable = False
        self._retry_attempts = retry_attempts
        # 서킷 개방 시 매 검색이 30초 타임아웃까지 기다리지 않고 즉시 로컬/identity 로 우회.
        self._breaker = CircuitBreaker(
            fail_threshold=circuit_fail_threshold,
            cooldown_seconds=circuit_cooldown_seconds,
            name=f"reranker:{self._base_url}",
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _http_rerank(
        self, query: str, documents: List[str], top_k: int
    ) -> List[dict]:
        response = await self._client.post(
            f"{self._base_url}/rerank",
            # top_n 미지정 시 서버 기본값(10)으로 응답이 잘려, 후보 풀(50+) 중
            # 10개만 티어 판정에 도달하던 배선 버그 — 요청한 top_k 를 명시한다
            # (실사고: rerank_audit 도입 직후 감사 행 10개로 발견).
            json={
                "query": query, "texts": documents,
                "return_text": False, "top_n": top_k,
            },
        )
        response.raise_for_status()
        raw = response.json()
        results = []
        for item in raw:
            score = 1 / (1 + math.exp(-float(item["score"])))
            results.append({"index": item["index"], "score": score})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    async def rerank(
        self, query: str, documents: List[str], top_k: int = 10
    ) -> List[dict]:
        try:
            # 일시적 blip 은 재시도로 흡수, 서버 다운은 서킷이 열려 fast-fail → 아래 degrade.
            return await retry_async(
                lambda: self._http_rerank(query, documents, top_k),
                attempts=self._retry_attempts,
                breaker=self._breaker,
                name="reranker",
            )
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
