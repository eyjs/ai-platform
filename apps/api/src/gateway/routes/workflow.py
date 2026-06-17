"""워크플로우(순차적 챗봇) 엔드포인트: /workflows, /workflow/start, /workflow/advance."""

import uuid

from fastapi import APIRouter, HTTPException, Request

from src.gateway.models import WorkflowAdvanceRequest, WorkflowStartRequest
from src.gateway.routes.helpers import (
    _authenticate,
    _get_app_state,
    _step_to_response,
    logger,
)

router = APIRouter()


@router.get("/workflows")
async def list_workflows(request: Request):
    """사용 가능한 워크플로우 목록."""
    state = _get_app_state(request)
    workflows = await state.workflow_store.list_all_async()
    return [
        {"id": w.id, "name": w.name, "steps": len(w.steps)}
        for w in workflows
    ]


@router.post("/workflow/start")
async def workflow_start(req: WorkflowStartRequest, request: Request):
    """워크플로우를 시작하고 첫 번째 스텝을 반환한다."""
    state = _get_app_state(request)
    user_ctx = await _authenticate(request)

    session_id = req.session_id or str(uuid.uuid4())

    logger.info(
        "workflow_start_request",
        workflow_id=req.workflow_id,
        session_id=session_id,
        user_id=user_ctx.user_id,
    )

    try:
        # 이 직접 라우트는 Profile/ExecutionPlan을 거치지 않으므로 context_adapter를
        # 전달하지 않는다(grounding 없이 진행). 챗 경로(graph_executor)가 실사용 경로다.
        result = await state.workflow_engine.start(req.workflow_id, session_id)
    except Exception as e:
        logger.error("workflow_start_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    response = _step_to_response(result)
    response["session_id"] = session_id
    response["workflow_id"] = req.workflow_id
    return response


@router.post("/workflow/advance")
async def workflow_advance(req: WorkflowAdvanceRequest, request: Request):
    """사용자 입력을 받아 다음 스텝으로 진행한다."""
    state = _get_app_state(request)
    await _authenticate(request)

    try:
        result = await state.workflow_engine.advance(req.session_id, req.input)
    except Exception as e:
        logger.error("workflow_advance_error", error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

    return _step_to_response(result)
