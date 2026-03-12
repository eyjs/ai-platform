"""Response Policy Guard: strict/balanced 모드 전환."""

import logging

from src.domain.models import ResponsePolicy
from src.safety.base import GuardrailContext, GuardrailResult

logger = logging.getLogger(__name__)

NO_DOCUMENT_BLOCK_MSG = "관련 문서를 찾지 못했습니다. 정확한 답변을 위해 문서를 확인해주세요."


class ResponsePolicyGuard:
    """응답 정책 가드레일.

    strict: 문서 근거 없으면 답변 거부
    balanced: 문서 없으면 LLM 자체 지식으로 폴백
    """

    name = "response_policy"

    async def check(self, answer: str, context: GuardrailContext) -> GuardrailResult:
        if context.response_policy != ResponsePolicy.STRICT:
            return GuardrailResult.passed()

        if not context.source_documents:
            return GuardrailResult.block(NO_DOCUMENT_BLOCK_MSG)

        return GuardrailResult.passed()
