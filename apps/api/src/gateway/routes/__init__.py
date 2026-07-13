"""Gateway 라우트 패키지 (Step22 G25 god-file 분할).

router.py(1327줄)를 도메인 경계별 모듈로 순수 이동했다. 각 모듈은 자체 APIRouter를
선언하고 공용 로직은 helpers만 의존한다(routes/* 상호 import 금지 → 순환 방지).
여기서 단일 `gateway_router`로 조합한다. include 순서는 분할 전 등록 순서와 동일하게
유지하여 라우트 인벤토리(경로/순서)를 불변으로 보존한다.
"""

from fastapi import APIRouter

from src.gateway.routes import (
    admin,
    chat,
    classify,
    feedback,
    ingest,
    inspect,
    public,
    session,
    workflow,
)
from src.gateway.routes.helpers import (
    APP_VERSION,
    wait_for_pending_requests,
)

gateway_router = APIRouter()

# 분할 전 router.py 정의 순서를 그대로 보존한다(라우트 인벤토리 불변).
gateway_router.include_router(public.router)
gateway_router.include_router(chat.router)
gateway_router.include_router(classify.router)
gateway_router.include_router(ingest.router)
gateway_router.include_router(workflow.router)
gateway_router.include_router(admin.router)
gateway_router.include_router(feedback.router)
gateway_router.include_router(session.router)
# 역방향 분석 (청크/문서 역조회 — 분할 후 신설, 인벤토리 끝에 추가)
gateway_router.include_router(inspect.router)

__all__ = ["gateway_router", "APP_VERSION", "wait_for_pending_requests"]
