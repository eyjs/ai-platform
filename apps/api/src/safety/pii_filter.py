"""PII Filter: 개인정보 감지 및 마스킹."""

import logging

from src.locale.bundle import get_locale
from src.safety.base import Guardrail, GuardrailContext, GuardrailResult

logger = logging.getLogger(__name__)


class PIIFilterGuard:
    """개인정보 감지 및 마스킹 가드레일."""

    name = "pii_filter"

    async def check(self, answer: str, context: GuardrailContext) -> GuardrailResult:
        detected = []
        masked_answer = answer
        suffix = get_locale().label("pii_masked_suffix")

        for pattern, pii_type in get_locale().pii_patterns:
            matches = pattern.findall(masked_answer)
            if matches:
                detected.append(pii_type)
                masked_answer = pattern.sub(f'[{pii_type} {suffix}]', masked_answer)

        if detected:
            reason = f"개인정보 감지: {', '.join(detected)}"
            logger.warning("PII detected in answer: %s", reason)
            return GuardrailResult.warn(reason, masked_answer)

        return GuardrailResult.passed()
