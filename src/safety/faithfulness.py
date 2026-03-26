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
from src.safety.base import GuardrailContext, GuardrailResult

logger = logging.getLogger(__name__)

_CITATION_PATTERN = re.compile(r'[\w가-힣]+\.(?:pdf|csv|md|xlsx|docx)', re.IGNORECASE)

_DEEP_EVAL_PROMPT = """아래 답변이 제공된 소스 문서에 근거하는지 판단하세요.

[소스 문서]
{sources}

[답변]
{answer}

JSON으로 응답하세요:
{{"faithful": true/false, "reason": "판단 근거 (1문장)"}}"""


class FaithfulnessGuard:
    """숫자/인용 검증 가드레일."""

    name = "faithfulness"

    def __init__(self, router_llm: Optional[LLMProvider] = None):
        self._llm = router_llm

    async def check(self, answer: str, context: GuardrailContext) -> GuardrailResult:
        if not context.source_documents:
            return GuardrailResult.passed()

        # --- Quick-check 1: 숫자 멤버십 ---
        answer_numbers = self._extract_numbers(answer)
        if answer_numbers:
            source_text = " ".join(
                doc.get("content", "") for doc in context.source_documents
            )
            source_numbers = self._extract_numbers(source_text)
            unverified = [n for n in answer_numbers if n not in source_numbers]
            if unverified:
                warning = f"답변의 숫자 {unverified}이(가) 참고 문서에서 확인되지 않았습니다."
                logger.warning("faithfulness_number_missing: %s", warning)
                modified = answer + f"\n\n[주의: {warning}]"
                return GuardrailResult.warn(warning, modified)

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

        return GuardrailResult.passed()

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
                warning = f"숫자 '{a}'와 '{b}'가 같은 문서 청크에 공존하지 않습니다."
                logger.warning("faithfulness_cooccurrence: %s", warning)
                return GuardrailResult.warn(
                    warning, None,
                )
        return None

    @staticmethod
    def _check_citations(answer: str, docs: list[dict]) -> Optional[GuardrailResult]:
        """답변에서 언급한 문서명이 소스에 존재하는지 검증."""
        cited = _CITATION_PATTERN.findall(answer)
        if not cited:
            return None

        source_files = {
            doc.get("file_name", "") for doc in docs
        }
        for cite in cited:
            if cite not in source_files:
                warning = f"인용된 문서 '{cite}'가 참고 문서에 없습니다."
                logger.warning("faithfulness_citation: %s", warning)
                return GuardrailResult.warn(warning, None)
        return None

    async def _deep_eval(
        self, answer: str, context: GuardrailContext,
    ) -> Optional[GuardrailResult]:
        """LLM으로 근거 검증 (STRICT 전용)."""
        try:
            sources = "\n---\n".join(
                doc.get("content", "")[:500] for doc in context.source_documents[:5]
            )
            prompt = _DEEP_EVAL_PROMPT.format(sources=sources, answer=answer)
            result = await self._llm.generate_json(prompt)

            if not result.get("faithful", True):
                reason = result.get("reason", "근거 불충분")
                logger.warning("faithfulness_deep_eval: %s", reason)
                return GuardrailResult.warn(
                    f"LLM 근거 검증 실패: {reason}", None,
                )
        except Exception as e:
            logger.warning("faithfulness_deep_eval_error: %s", str(e))
        return None

    @staticmethod
    def _extract_numbers(text: str) -> set[str]:
        """텍스트에서 의미 있는 숫자를 추출한다."""
        patterns = [
            r'\d{1,3}(?:,\d{3})+',  # 1,000,000
            r'\d+(?:\.\d+)?%',       # 50.5%
            r'\d+(?:\.\d+)?만',      # 100만
            r'\d+(?:\.\d+)?억',      # 10억
            r'\d+(?:\.\d+)?원',      # 5000원
            r'\d+(?:~\d+)',          # 8~9
            r'\d+[급종호조항]',       # 8급, 1종
        ]
        numbers = set()
        for pattern in patterns:
            numbers.update(re.findall(pattern, text))
        # 단독 숫자 (3자리 이상만)
        for match in re.findall(r'\b(\d{3,})\b', text):
            numbers.add(match)
        return numbers
