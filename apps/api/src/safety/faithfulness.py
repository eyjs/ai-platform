"""Faithfulness Guard: 숫자 co-occurrence + 인용 검증 + LLM deep eval.

Quick-check (항상):
  1. 숫자 멤버십 — 답변의 숫자가 소스에 존재하는지
  2. 숫자 co-occurrence — 답변의 숫자 쌍이 같은 청크에 공존하는지
  3. 인용 검증 — 답변이 언급한 문서명이 소스에 존재하는지

Deep eval (STRICT + router_llm 있을 때만):
  4. LLM 근거 검증 — "이 답변이 소스에 근거하는가?"
"""

import logging
import re
from itertools import combinations
from typing import Optional

from src.infrastructure.providers.base import LLMProvider
from src.locale.bundle import get_locale
from src.safety.base import GuardrailContext, GuardrailResult

logger = logging.getLogger(__name__)


class FaithfulnessGuard:
    """숫자/인용 검증 가드레일."""

    name = "faithfulness"
    MAX_FAITHFULNESS_CHECKS = 3

    def __init__(self, router_llm: Optional[LLMProvider] = None):
        self._llm = router_llm
        exts = "|".join(get_locale().citation_extensions)
        self._citation_re = re.compile(rf'[\w가-힣]+\.(?:{exts})', re.IGNORECASE)
        self._check_count: dict[str, int] = {}  # 세션별 체크 횟수 추적

    async def check(self, answer: str, context: GuardrailContext) -> GuardrailResult:
        # 세션별 체크 횟수 확인 (무한 루프 방지)
        session_id = getattr(context, 'session_id', 'default')
        current_count = self._check_count.get(session_id, 0)
        if current_count >= self.MAX_FAITHFULNESS_CHECKS:
            logger.warning(
                "faithfulness_max_checks_exceeded",
                session_id=session_id,
                count=current_count,
                max_checks=self.MAX_FAITHFULNESS_CHECKS,
            )
            return GuardrailResult.passed(score=None)

        # 체크 횟수 증가
        self._check_count[session_id] = current_count + 1

        if not context.source_documents:
            # source_documents 가 없으면 측정 불가 → score=None
            return GuardrailResult.passed(score=None)

        # --- Quick-check 1: 숫자 멤버십 ---
        answer_numbers = self._extract_numbers(answer)
        if answer_numbers:
            source_text = " ".join(
                doc.get("content", "") for doc in context.source_documents
            )
            source_numbers = self._extract_numbers(source_text)
            unverified = [n for n in answer_numbers if n not in source_numbers]
            if unverified:
                # 완화 검증: 단위/접미사 제거 후 숫자값만 비교
                # "1항" → "1", 소스 "1." → "1" 이면 pass
                source_bare = self._extract_bare_numbers(source_text)
                still_unverified = [
                    n for n in unverified
                    if not set(re.findall(r'\d+', n)).issubset(source_bare)
                ]
                if still_unverified:
                    warning = get_locale().message("number_missing", numbers=str(still_unverified))
                    logger.warning("faithfulness_number_missing: %s", warning)
                    modified = answer + f"\n\n[주의: {warning}]"
                    # Quick-check 실패 → 0.5
                    return GuardrailResult.warn(warning, modified, score=0.5)

            # --- Quick-check 2: 숫자 co-occurrence ---
            if len(answer_numbers) >= 2:
                result = self._check_cooccurrence(answer_numbers, context.source_documents)
                if result:
                    return result

        # --- Quick-check 3: 인용 검증 ---
        citation_result = self._check_citations(answer, context.source_documents)
        if citation_result:
            return citation_result

        # --- Deep eval (STRICT only) ---
        if context.response_policy == "strict" and self._llm:
            deep_result = await self._deep_eval(answer, context)
            if deep_result:
                return deep_result

        # 모든 검증 통과 → 1.0
        return GuardrailResult.passed(score=1.0)

    @staticmethod
    def _check_cooccurrence(
        answer_numbers: set[str], docs: list[dict],
    ) -> Optional[GuardrailResult]:
        """답변의 숫자 쌍이 같은 청크에 공존하는지 검증."""
        for a, b in combinations(answer_numbers, 2):
            found_together = False
            for doc in docs:
                content = doc.get("content", "")
                if a in content and b in content:
                    found_together = True
                    break
            if not found_together:
                warning = get_locale().message("cooccurrence_fail", a=a, b=b)
                logger.warning("faithfulness_cooccurrence: %s", warning)
                return GuardrailResult.warn(
                    warning, None, score=0.5,
                )
        return None

    def _check_citations(self, answer: str, docs: list[dict]) -> Optional[GuardrailResult]:
        """답변에서 언급한 문서명이 소스에 존재하는지 검증."""
        cited = self._citation_re.findall(answer)
        if not cited:
            return None

        source_files = {
            doc.get("file_name", "") for doc in docs
        }
        for cite in cited:
            if cite not in source_files:
                warning = get_locale().message("citation_missing", cite=cite)
                logger.warning("faithfulness_citation: %s", warning)
                return GuardrailResult.warn(warning, None, score=0.5)
        return None

    async def _deep_eval(
        self, answer: str, context: GuardrailContext,
    ) -> Optional[GuardrailResult]:
        """LLM으로 근거 검증 (STRICT 전용)."""
        try:
            sources = "\n---\n".join(
                doc.get("content", "")[:500] for doc in context.source_documents[:5]
            )
            prompt = get_locale().prompt("deep_eval", sources=sources, answer=answer)
            result = await self._llm.generate_json(prompt)

            if not result.get("faithful", True):
                reason = result.get("reason", "근거 불충분")
                logger.warning("faithfulness_deep_eval: %s", reason)
                return GuardrailResult.warn(
                    get_locale().message("deep_eval_fail", reason=reason),
                    None,
                    score=0.3,
                )
        except Exception as e:
            logger.warning("faithfulness_deep_eval_error: %s", str(e))
        return None

    @staticmethod
    def _extract_numbers(text: str) -> set[str]:
        """텍스트에서 의미 있는 숫자를 추출한다."""
        numbers = set()
        for pattern in get_locale().number_patterns:
            numbers.update(pattern.findall(text))
        # 단독 숫자 (3자리 이상만)
        for match in re.findall(r'\b(\d{3,})\b', text):
            numbers.add(match)
        return numbers

    @staticmethod
    def _extract_bare_numbers(text: str) -> set[str]:
        """숫자값만 추출 (단위/접미사 제거). 숫자 매칭 완화용."""
        return set(re.findall(r'\d+', text))
