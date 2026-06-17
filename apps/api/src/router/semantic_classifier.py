"""공통 의미 분류기 (Semantic Classifier).

"사용자 입력 + 후보(candidate) + 맥락 → 어느 후보인가?"를 LLM으로 판단하는
단일 공유 컴포넌트. 키워드/부분문자열 분류를 대체하며, 워크플로우 분기·진입 등
분류가 필요한 모든 지점에서 재사용한다(서비스마다 키워드 테이블을 다시 짜지 않도록).

원칙:
- fast-path 우선: 정확/대소문자 무시 라벨 매칭(버튼 탭 등)은 LLM 없이 즉시 반환.
- LLM은 자유입력/모호할 때만 호출(지연·비용 가드). 경량 router_llm 주입.
- 엄격 파싱: 후보 label과 정확히 일치할 때만 채택(환각 차단). threshold 미만/실패 → None.
- LLM 미주입 시 fast-path만 수행 → 키워드/재안내 등 호출부 폴백과 자연 호환.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional, Sequence

from src.config import settings
from src.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Candidate:
    """분류 후보. label은 반환 식별자(workflow_id/intent/branch 옵션), description은 의미판단용."""

    label: str
    description: str = ""


@dataclass
class ClassifyResult:
    label: Optional[str]
    confidence: float = 0.0


class SemanticClassifier:
    """후보 중 사용자 입력의 의도에 맞는 하나를 택하거나, 없으면 None."""

    def __init__(self, llm=None) -> None:
        # 경량 분류용 LLMProvider (router_llm). 미주입 시 fast-path만 동작.
        self._llm = llm

    async def classify(
        self,
        query: str,
        candidates: Sequence[Candidate],
        *,
        context: str = "",
        threshold: float = 0.6,
    ) -> ClassifyResult:
        labels = [c.label for c in candidates]
        if not labels:
            return ClassifyResult(None, 0.0)

        q = (query or "").strip()

        # 1) fast-path: 정확/대소문자 무시 라벨 매칭 (버튼·명시 입력) → LLM 미호출
        for c in candidates:
            if q == c.label or q.lower() == c.label.lower():
                return ClassifyResult(c.label, 1.0)

        # 2) LLM 미주입 → fast-path만 (하위호환: 호출부가 기존 폴백 수행)
        if not self._llm:
            return ClassifyResult(None, 0.0)

        # 3) LLM 의미 분류
        result = await self._classify_with_llm(q, candidates, context)
        if result.label in labels and result.confidence >= threshold:
            return result
        return ClassifyResult(None, result.confidence)

    async def _classify_with_llm(
        self, query: str, candidates: Sequence[Candidate], context: str,
    ) -> ClassifyResult:
        options_text = "\n".join(
            f"- {c.label}" + (f": {c.description}" if c.description else "")
            for c in candidates
        )
        prompt = (
            "사용자의 말이 아래 후보 중 어디에 해당하는지 '의미'로 판단하세요. "
            "단순 단어 일치가 아니라 맥락과 의도로 고르세요.\n\n"
            + (f"[맥락]\n{context}\n\n" if context else "")
            + f"[사용자 입력]\n{query}\n\n"
            + f"[후보]\n{options_text}\n\n"
            + '정확히 하나를 고르되, 어느 것에도 분명히 해당하지 않으면 label을 "NONE"으로 하세요.\n'
            + 'JSON으로만 답변: {"label": "<후보 label 또는 NONE>", "confidence": 0.0~1.0}'
        )
        try:
            result = await asyncio.wait_for(
                self._llm.generate_json(prompt),
                timeout=settings.planner_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("semantic_classify_timeout", query=query[:50])
            return ClassifyResult(None, 0.0)
        except Exception as e:  # noqa: BLE001
            logger.warning("semantic_classify_error", error=str(e))
            return ClassifyResult(None, 0.0)

        label = str(result.get("label", "")).strip()
        try:
            confidence = float(result.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        if label.upper() == "NONE" or not label:
            return ClassifyResult(None, confidence)
        # 후보 label과 정확 일치만 채택(환각 차단)
        for c in candidates:
            if label == c.label:
                logger.info(
                    "semantic_classify",
                    query=query[:50], label=label, confidence=confidence,
                )
                return ClassifyResult(c.label, confidence)
        logger.warning("semantic_classify_unknown_label", label=label[:50])
        return ClassifyResult(None, confidence)
