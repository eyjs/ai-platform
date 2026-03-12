"""Layer 0: Context Resolver — 대명사 해소 + 대화 맥락 해석.

PatternBasedResolver → LLMBasedResolver 2-tier 체인.
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

from src.infrastructure.providers.base import LLMProvider

logger = logging.getLogger(__name__)

PATTERN_CONFIDENCE_THRESHOLD = 0.9
LLM_CHANGED_CONFIDENCE = 0.7
LLM_HISTORY_TURNS = 4


@dataclass
class ResolutionResult:
    """맥락 해석 결과."""

    resolved_query: str
    original_query: str
    confidence: float
    method: str  # "pattern" | "llm" | "passthrough"


class PatternBasedResolver:
    """패턴 기반 대명사 해소 (빠르고 정확)."""

    PRONOUN_PATTERNS = [
        (r"^(그|이|저)(것|거|건|게)\s", 0.9),
        (r"^(그|이|저)(문서|상품|보험|약관)", 0.9),
        (r"^(위|앞|방금)\s*(것|거|내용)", 0.85),
        (r"^(더|또)\s*(알려|설명|자세)", 0.8),
    ]

    def resolve(
        self, query: str, history: List[dict],
    ) -> Optional[ResolutionResult]:
        if not history:
            return None

        for pattern, confidence in self.PRONOUN_PATTERNS:
            if re.search(pattern, query):
                # 가장 최근 사용자 질문에서 주제 추출
                prev_query = self._find_last_user_query(history)
                if prev_query:
                    resolved = re.sub(pattern, prev_query + " ", query).strip()
                    return ResolutionResult(
                        resolved_query=resolved,
                        original_query=query,
                        confidence=confidence,
                        method="pattern",
                    )
        return None

    @staticmethod
    def _find_last_user_query(history: List[dict]) -> Optional[str]:
        for turn in reversed(history):
            if turn.get("role") == "user":
                return turn.get("content", "")
        return None


class LLMBasedResolver:
    """LLM 기반 대명사 해소 (패턴 실패 시 폴백)."""

    def __init__(self, llm: LLMProvider):
        self._llm = llm

    async def resolve(
        self, query: str, history: List[dict],
    ) -> ResolutionResult:
        if not history:
            return ResolutionResult(
                resolved_query=query, original_query=query,
                confidence=1.0, method="passthrough",
            )

        history_text = "\n".join(
            f"{t['role']}: {t['content']}" for t in history[-LLM_HISTORY_TURNS:]
        )

        try:
            result = await self._llm.generate_json(
                f"대화 이력:\n{history_text}\n\n"
                f"현재 질문: {query}\n\n"
                f"현재 질문에 대명사나 생략이 있으면 완전한 질문으로 바꾸세요.\n"
                f"없으면 원래 질문을 그대로 반환하세요.\n"
                f'JSON: {{"resolved": "완전한 질문", "changed": true/false}}'
            )
            resolved = result.get("resolved", query)
            changed = result.get("changed", False)
            return ResolutionResult(
                resolved_query=resolved,
                original_query=query,
                confidence=LLM_CHANGED_CONFIDENCE if changed else 1.0,
                method="llm" if changed else "passthrough",
            )
        except Exception as e:
            logger.warning("LLM resolver failed: %s", e)
            return ResolutionResult(
                resolved_query=query, original_query=query,
                confidence=1.0, method="passthrough",
            )


class ChainResolver:
    """Pattern → LLM 순서로 해소를 시도하는 체인."""

    def __init__(self, llm: LLMProvider):
        self._pattern = PatternBasedResolver()
        self._llm = LLMBasedResolver(llm)

    async def resolve(
        self, query: str, history: List[dict],
    ) -> ResolutionResult:
        # 1. 패턴 기반 (빠르고 정확)
        result = self._pattern.resolve(query, history)
        if result and result.confidence >= PATTERN_CONFIDENCE_THRESHOLD:
            return result

        # 2. LLM 기반 (폴백)
        return await self._llm.resolve(query, history)
