"""AI Platform FastAPI 진입점.

Lifespan: bootstrap.create_app_state()로 컴포넌트 초기화.
인프라 = PostgreSQL only (Redis 없음).
"""

import os
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.bootstrap import create_app_state, seed_dev_api_keys, shutdown, start_cleanup_task
from src.config import settings
from src.gateway.router import APP_VERSION, gateway_router
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
        interval=settings.cache_cleanup_interval,
    )

    await seed_dev_api_keys(state.vector_store.pool)

    # app.state에 컴포넌트 등록
    app.state.settings = state.settings
    app.state.auth_service = state.auth_service
    app.state.vector_store = state.vector_store
    app.state.fact_store = state.fact_store
    app.state.session_memory = state.session_memory
    app.state.cache = state.cache
    app.state.profile_store = state.profile_store
    app.state.tool_registry = state.tool_registry
    app.state.ai_router = state.ai_router
    app.state.agent = state.agent
    app.state.ingest_pipeline = state.ingest_pipeline
    app.state.provider_factory = state.provider_factory

    logger.info("startup_complete")
    yield

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

_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir), html=True), name="static")
