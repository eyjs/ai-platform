"""Faithfulness Guard: 숫자/인용 검증.

답변에 등장하는 숫자가 출처 문서에 실제로 존재하는지 검증.
"""

import logging
import re

from src.safety.base import Guardrail, GuardrailContext, GuardrailResult

logger = logging.getLogger(__name__)


class FaithfulnessGuard:
    """숫자/인용 검증 가드레일."""

    name = "faithfulness"

    async def check(self, answer: str, context: GuardrailContext) -> GuardrailResult:
        if not context.source_documents:
            # 문서 없이 생성된 답변은 검증 불가
            if context.response_policy == "strict":
                return GuardrailResult.block(
                    "관련 문서를 찾지 못했습니다. 정확한 답변을 위해 문서를 확인해주세요."
                )
            return GuardrailResult.passed()

        # 답변에서 숫자 추출
        answer_numbers = self._extract_numbers(answer)
        if not answer_numbers:
            return GuardrailResult.passed()

        # 출처 문서에서 숫자 추출
        source_text = " ".join(
            doc.get("content", "") for doc in context.source_documents
        )
        source_numbers = self._extract_numbers(source_text)

        # 검증: 답변의 숫자가 출처에 있는지
        unverified = []
        for num in answer_numbers:
            if num not in source_numbers:
                unverified.append(num)

        if unverified:
            warning = (
                f"답변에 포함된 숫자 {unverified} 중 일부가 "
                f"참고 문서에서 확인되지 않았습니다."
            )
            logger.warning("Faithfulness warning: %s", warning)
            modified = answer + f"\n\n[주의: {warning}]"
            return GuardrailResult.warn(warning, modified)

        return GuardrailResult.passed()

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
        ]
        numbers = set()
        for pattern in patterns:
            numbers.update(re.findall(pattern, text))
        # 단독 숫자 (3자리 이상만 — 1, 2 같은 건 제외)
        for match in re.findall(r'\b(\d{3,})\b', text):
            numbers.add(match)
        return numbers
