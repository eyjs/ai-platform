"""Layer 0: Context Resolver — 대명사 해소 + 대화 맥락 해석.

PatternBasedResolver → LLMBasedResolver 2-tier 체인.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from src.infrastructure.providers.base import LLMProvider
from src.locale.bundle import get_locale

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

    def resolve(
        self, query: str, history: List[dict],
    ) -> Optional[ResolutionResult]:
        if not history:
            return None

        for pattern, confidence in get_locale().pronoun_patterns():
            if pattern.search(query):
                # 패턴은 감지용으로만 사용 — 실제 해소는 LLM에 위임
                # confidence를 threshold 미만으로 설정하여 ChainResolver가 LLM으로 넘기게 함
                return ResolutionResult(
                    resolved_query=query,
                    original_query=query,
                    confidence=0.5,  # < PATTERN_CONFIDENCE_THRESHOLD (0.9)
                    method="pattern_detected",
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
            prompt = get_locale().prompt(
                "context_resolver_llm",
                history_text=history_text,
                query=query,
            )
            result = await self._llm.generate_json(prompt)
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
