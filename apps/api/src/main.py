"""AI Platform FastAPI 진입점.

Lifespan: bootstrap.create_app_state()로 컴포넌트 초기화.
인프라 = PostgreSQL only (Redis 없음).
"""

import os
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.bootstrap import create_app_state, seed_dev_api_keys, shutdown, start_cleanup_task
from src.common.exceptions import INFRA, PIPELINE, AppError
from src.config import settings
from src.gateway.admin_router import admin_router
from src.gateway.report_router import report_router
from src.gateway.router import APP_VERSION, gateway_router, wait_for_pending_requests
from src.gateway.webhook_router import webhook_router
from src.observability.logging import configure_logging, get_logger

# 로깅 설정: Docker/프로덕션에서는 JSON, 로컬 개발에서는 사람이 읽기 쉬운 포맷
_json_logs = os.getenv("AIP_LOG_FORMAT", "json") == "json"
_log_level = os.getenv("AIP_LOG_LEVEL", "INFO")
configure_logging(level=_log_level, json_format=_json_logs)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 생명주기: 초기화 -> 실행 -> 정리."""
    logger.info(
        "startup",
        mode=settings.provider_mode.value,
        database=settings.database_url.split("@")[-1],
        log_format="json" if _json_logs else "human",
        log_level=_log_level,
    )

    state = await create_app_state(settings)

    state.cleanup_task = start_cleanup_task(
        cache=state.cache,
        session_memory=state.session_memory,
        job_queue=state.job_queue,
        interval=settings.cache_cleanup_interval,
    )

    # Task 009: 신규 서비스 기동
    if state.request_log_service:
        await state.request_log_service.start()
    if state.response_cache_service:
        await state.response_cache_service.start_sweeper(interval_seconds=60)
    # Task 014: 30일 auto-purge sweeper (1시간 주기면 충분)
    if state.feedback_service:
        await state.feedback_service.start_sweeper(interval_seconds=3600)

    # Task 004: Saju Report QueueWorker 시작
    if state.saju_report_worker:
        import asyncio
        asyncio.create_task(state.saju_report_worker.start())
        logger.info("saju_report_worker_started", worker_id=state.saju_report_worker._worker_id)

    await seed_dev_api_keys(state.vector_store.pool)

    # app.state에 컴포넌트 등록 (AppState 필드 자동 매핑)
    _INTERNAL_FIELDS = {"cleanup_task", "providers", "saju_report_worker"}
    for field_name in state.__dataclass_fields__:
        if field_name not in _INTERNAL_FIELDS:
            setattr(app.state, field_name, getattr(state, field_name))

    logger.info("startup_complete")
    yield

    # Graceful shutdown: Profile watcher 중지 + 진행 중 요청 완료 대기
    if hasattr(state, 'profile_store') and state.profile_store:
        state.profile_store.stop_watcher()
    await wait_for_pending_requests(timeout=30.0)

    await shutdown(state)


app = FastAPI(
    title="AI Platform",
    description="Universal Agent Platform - Profile 기반 도메인별 AI 에이전트",
    version=APP_VERSION,
    lifespan=lifespan,
)

_cors_origins = settings.cors_origins or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials="*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(gateway_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(report_router, prefix="/api")
app.include_router(webhook_router, prefix="/api")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """Layer-Aware 예외를 잡아서 구조화 로그 + 안전한 HTTP 응답으로 변환."""
    logger.error(
        "app_error",
        layer=exc.layer,
        component=exc.component,
        error_code=exc.error_code,
        error=str(exc),
        details=exc.details,
        exc_info=True,
    )
    status_code = 500 if exc.layer in {INFRA, PIPELINE} else 400
    return JSONResponse(
        status_code=status_code,
        content={"error": "요청 처리 중 문제가 발생했습니다.", "code": exc.error_code},
    )

_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")
