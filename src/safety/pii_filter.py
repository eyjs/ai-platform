"""PII Filter: 개인정보 감지 및 마스킹."""

import logging
import re

from src.safety.base import Guardrail, GuardrailContext, GuardrailResult

logger = logging.getLogger(__name__)


class PIIFilterGuard:
    """개인정보 감지 및 마스킹 가드레일."""

    name = "pii_filter"

    PII_PATTERNS = [
        (r'\d{6}[-\s]?\d{7}', '주민등록번호'),         # 주민번호
        (r'\d{3}[-\s]?\d{4}[-\s]?\d{4}', '전화번호'),  # 전화번호
        (r'\d{3}[-\s]?\d{2}[-\s]?\d{5}', '사업자번호'), # 사업자번호
    ]

    async def check(self, answer: str, context: GuardrailContext) -> GuardrailResult:
        detected = []
        masked_answer = answer

        for pattern, pii_type in self.PII_PATTERNS:
            matches = re.findall(pattern, masked_answer)
            if matches:
                detected.append(pii_type)
                masked_answer = re.sub(pattern, f'[{pii_type} 마스킹됨]', masked_answer)

        if detected:
            reason = f"개인정보 감지: {', '.join(detected)}"
            logger.warning("PII detected in answer: %s", reason)
            return GuardrailResult.warn(reason, masked_answer)

        return GuardrailResult.passed()
