"""의도 분류 엔드포인트: POST /classify-intent.

백엔드 오케스트레이터가 후보(candidates)를 보내면 SemanticClassifier.classify를 재사용해
어느 의도인지 판단하여 { intent, confidence }를 반환한다.
분류기 로직은 수정하지 않고 재사용만 한다(router/semantic_classifier.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.gateway.models import ClassifyIntentRequest, ClassifyIntentResponse
from src.gateway.routes.helpers import _authenticate, _check_rate_limit, _get_app_state, logger
from src.router.semantic_classifier import Candidate

router = APIRouter()


def _flatten_history(history: list[dict] | str) -> str:
    """history를 분류기의 context 문자열로 평탄화한다."""
    if isinstance(history, str):
        return history
    parts = []
    for turn in history:
        role = turn.get("role", "")
        content = turn.get("content", "")
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


@router.post("/classify-intent", response_model=ClassifyIntentResponse)
async def classify_intent(req: ClassifyIntentRequest, request: Request):
    """사용자 메시지를 candidates 중 하나의 의도로 분류한다.

    - candidates는 백엔드가 주입 (서버는 고정 intent 테이블 없음).
    - LLM 미주입/타임아웃/실패 시 { intent: null, confidence: 0 } 반환 (흐름 차단 금지).
    - 인증 필수 (X-API-Key 헤더).
    """
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)
    await _check_rate_limit(request, user_ctx)

    # candidates 없으면 즉시 null 반환
    if not req.candidates:
        return ClassifyIntentResponse(intent=None, confidence=0.0)

    # app_state에서 공유 분류기 획득 (bootstrap에서 SemanticClassifier(router_llm)로 생성됨)
    classifier = getattr(state, "classifier", None)
    if classifier is None:
        # 분류기 미주입 시 graceful fallback (흐름 차단 금지)
        return ClassifyIntentResponse(intent=None, confidence=0.0)

    # history → context 문자열
    context_text = _flatten_history(req.history)

    # 공유 SemanticClassifier로 분류
    try:
        candidates = [Candidate(label=c.label, description=c.description) for c in req.candidates]
        result = await classifier.classify(
            query=req.message,
            candidates=candidates,
            context=context_text,
            threshold=req.threshold,
        )
        logger.info(
            "classify_intent",
            intent=result.label,
            confidence=result.confidence,
            user_id=user_ctx.user_id,
        )
        return ClassifyIntentResponse(intent=result.label, confidence=result.confidence)
    except Exception as e:  # noqa: BLE001
        # 분류 실패 시 흐름 차단 금지 — graceful fallback
        logger.warning("classify_intent_error", error=str(e))
        return ClassifyIntentResponse(intent=None, confidence=0.0)
