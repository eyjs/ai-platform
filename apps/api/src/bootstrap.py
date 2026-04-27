"""앱 부트스트랩: 컴포넌트 초기화 + 정리.

main.py lifespan에서 분리. 13개 컴포넌트 초기화를 단계별 함수로 정리.
"""

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Optional

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))

from src.locale.bundle import LocaleBundle, set_locale
from src.agent.chat_model_factory import create_chat_model
from src.agent.graph_executor import GraphExecutor
from src.agent.profile_store import ProfileStore
from src.config import Settings
from src.gateway.access_policy import AccessPolicyStore
from src.gateway.auth import AuthService
from src.gateway.rate_limiter import PGRateLimiter
from src.infrastructure.fact_store import FactStore
from src.infrastructure.job_queue import JobQueue
from src.infrastructure.memory.cache import PgCache
from src.infrastructure.memory.session import SessionMemory
from src.infrastructure.providers.factory import ProviderFactory
from src.infrastructure.providers.registry import ProviderRegistry
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.observability.request_log_service import RequestLogService
from src.router.provider_router import ProviderRouter
from src.services.feedback_service import FeedbackService
from src.services.response_cache import ResponseCacheService
from src.pipeline.ingest import IngestPipeline
from src.router.ai_router import AIRouter
from src.safety.faithfulness import FaithfulnessGuard
from src.safety.pii_filter import PIIFilterGuard
from src.safety.response_policy import ResponsePolicyGuard
from src.tools.internal.fact_lookup import FactLookupTool
from src.tools.internal.rag_search import RAGSearchTool
from src.tools.registry import ToolRegistry
from src.orchestrator.embedding_router import EmbeddingRouter
from src.orchestrator.llm_adapter import OrchestratorLLM
from src.orchestrator.orchestrator import MasterOrchestrator
from src.orchestrator.tenant import TenantService
from src.services.kms_graph_client import KmsGraphClient
from src.services.null_kms_client import NullKmsClient
from src.workflow.engine import WorkflowEngine
from src.workflow.store import WorkflowStore

logger = get_logger(__name__)


@dataclass
class AppState:
    """앱 런타임 상태. lifespan 동안 유지."""

    settings: Settings
    auth_service: AuthService
    vector_store: VectorStore
    fact_store: FactStore
    session_memory: SessionMemory
    cache: PgCache
    profile_store: ProfileStore
    tool_registry: ToolRegistry
    ai_router: AIRouter
    agent: GraphExecutor
    ingest_pipeline: IngestPipeline
    workflow_engine: WorkflowEngine
    workflow_store: WorkflowStore
    provider_factory: ProviderFactory
    job_queue: JobQueue
    rate_limiter: PGRateLimiter
    tenant_service: Optional[TenantService] = None
    orchestrator: Optional[MasterOrchestrator] = None

    # Task 009: 통합 서비스
    provider_registry: Optional[ProviderRegistry] = None
    provider_router: Optional[ProviderRouter] = None
    request_log_service: Optional[RequestLogService] = None
    response_cache_service: Optional[ResponseCacheService] = None
    # Task 014: 응답 피드백 서비스 + sweeper
    feedback_service: Optional[FeedbackService] = None
    # Task 004: Saju 리포트 서비스 + QueueWorker
    saju_report_service: Optional[Any] = None
    saju_report_worker: Optional[Any] = None

    # 내부 관리용
    cleanup_task: Optional[asyncio.Task] = None
    providers: list = field(default_factory=list)


def _check_profile_pattern_overlap(profiles):
    """프로필 간 intent_hints 패턴 중복을 검사하고 경고한다."""
    pattern_to_profiles: dict[str, list[str]] = {}
    for p in profiles:
        for hint in p.intent_hints:
            for pattern in hint.patterns:
                if len(pattern) >= 2:
                    pattern_to_profiles.setdefault(pattern, []).append(p.id)

    for pattern, profile_ids in pattern_to_profiles.items():
        if len(profile_ids) > 1:
            logger.warning(
                "profile_pattern_overlap",
                pattern=pattern,
                profiles=profile_ids,
            )


async def create_app_state(settings: Settings) -> AppState:
    """모든 컴포넌트를 초기화하고 AppState를 반환한다."""

    # 1. VectorStore (PostgreSQL + pgvector)
    vector_store = VectorStore(settings.database_url)
    await vector_store.connect(
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
    )
    pool = vector_store.pool
    logger.info("vectorstore_connected", pool_min=settings.pg_pool_min, pool_max=settings.pg_pool_max)

    # 0. 로케일 번들 로드
    locale_path = os.path.join(_SRC_DIR, "locale", f"{settings.locale}.yaml")
    locale_bundle = LocaleBundle.load(locale_path)
    set_locale(locale_bundle)
    logger.info("locale_loaded", locale=settings.locale, keys=locale_bundle.key_count)

    # 1-B. AccessPolicyStore (segment 접근 정책)
    access_policy = AccessPolicyStore(pool)
    await access_policy.load()

    # 2. AuthService
    auth_service = AuthService(
        pool=pool,
        jwt_secret=settings.jwt_secret,
        auth_required=settings.auth_required,
        access_policy=access_policy,
    )
    logger.info("auth_initialized", auth_required=settings.auth_required)

    # 3. FactStore + Memory
    fact_store = FactStore(pool)
    session_memory = SessionMemory(pool, default_ttl_seconds=3600)
    cache = PgCache(pool, default_ttl_seconds=300)

    # 4. Provider Factory
    provider_factory = ProviderFactory(settings)
    embedding_provider = provider_factory.get_embedding_provider()
    router_llm = provider_factory.get_router_llm()
    main_llm = provider_factory.get_main_llm()
    logger.info(
        "providers_initialized",
        embedding=type(embedding_provider).__name__,
        router_llm=type(router_llm).__name__,
        main_llm=type(main_llm).__name__,
    )

    reranker = None
    try:
        reranker = provider_factory.get_reranker()
        logger.info("reranker_initialized", type=type(reranker).__name__)
    except Exception as e:
        logger.warning("reranker_unavailable", error=str(e))

    # 5. Profile Store
    profile_store = ProfileStore(pool, seed_dir="seeds/profiles")
    seed_count = await profile_store.load_seeds()
    profiles = await profile_store.list_all()
    logger.info("profiles_loaded", seed_count=seed_count)

    # 프로필 간 intent_hints 패턴 중복 검사
    _check_profile_pattern_overlap(profiles)

    # 6. Tool Registry
    tool_registry = ToolRegistry()
    tool_registry.register(RAGSearchTool(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        reranker=reranker,
        router_llm=router_llm,
    ))
    tool_registry.register(FactLookupTool(fact_store=fact_store))
    logger.info("tools_registered", tools=tool_registry.tool_names)

    # 7. AI Router
    ai_router = AIRouter(router_llm)

    # 8. Guardrails
    guardrails = {
        "faithfulness": FaithfulnessGuard(router_llm=router_llm),
        "response_policy": ResponsePolicyGuard(),
        "pii_filter": PIIFilterGuard(),
    }

    # 9. ChatModel + GraphExecutor
    chat_model = None
    try:
        # MLX 서버 사용 시 모델명을 서버에서 자동 감지
        chat_model_name = settings.main_model
        if settings.main_llm_server_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        f"{settings.main_llm_server_url}/v1/models",
                    )
                    if resp.status_code == 200:
                        models = resp.json().get("data", [])
                        if models:
                            chat_model_name = models[0]["id"]
                            logger.info("chat_model_auto_detected", model=chat_model_name)
            except httpx.HTTPError as e:
                logger.warning("chat_model_detection_failed", error=str(e))

        chat_model = create_chat_model(
            provider_mode=settings.provider_mode,
            model_name=chat_model_name,
            ollama_host=settings.ollama_host,
            openai_api_key=settings.openai_api_key,
            server_url=settings.main_llm_server_url,
        )
        logger.info("chat_model_initialized", type=type(chat_model).__name__)
    except ImportError:
        logger.warning("chat_model_unavailable, agentic mode disabled")

    # 10. Workflow Engine (Agent보다 먼저 — Agent가 의존)
    workflow_store = WorkflowStore(pool=pool, seed_dir="seeds/workflows")
    await workflow_store.load_seeds()
    workflow_engine = WorkflowEngine(workflow_store)
    logger.info("workflows_loaded", count=workflow_store.count)

    # KMS 지식그래프 클라이언트 (미설정 시 NullKmsClient)
    if settings.kms_api_url and settings.kms_internal_key:
        kms_graph_client = KmsGraphClient(settings.kms_api_url, settings.kms_internal_key)
        logger.info("kms_graph_client_initialized", kms_api_url=settings.kms_api_url)
    else:
        kms_graph_client = NullKmsClient()
        logger.info("kms_graph_client_null", reason="KMS API 미설정")

    agent = GraphExecutor(
        main_llm=main_llm,
        tool_registry=tool_registry,
        guardrails=guardrails,
        chat_model=chat_model,
        workflow_engine=workflow_engine,
        kms_graph_client=kms_graph_client,
        vector_store=vector_store,
    )

    # 11. Parsing Provider + Ingest Pipeline
    parsing_provider = provider_factory.get_parsing_provider()
    logger.info("parser_initialized", type=type(parsing_provider).__name__)

    ingest_pipeline = IngestPipeline(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        settings=settings,
        parsing_provider=parsing_provider,
    )

    # 12. Job Queue (API는 enqueue만, 워커는 별도 프로세스)
    job_queue = JobQueue(pool)

    # 13. Rate Limiter (PostgreSQL Token Bucket)
    rate_limiter = PGRateLimiter(pool)
    logger.info("rate_limiter_initialized")

    # 14. TenantService
    tenant_service = TenantService(pool)

    # 15. MasterOrchestrator (최상위 모델)
    orchestrator = None
    orchestrator_llm = None
    if settings.orchestrator_enabled:
        api_key = settings.orchestrator_api_key or settings.openai_api_key
        # MLX/Ollama는 API Key 불필요
        needs_key = settings.orchestrator_provider in ("openai", "anthropic")
        if not needs_key or api_key:
            server_url = settings.orchestrator_server_url or settings.router_llm_server_url
            orchestrator_llm = OrchestratorLLM(
                provider=settings.orchestrator_provider,
                model=settings.orchestrator_model,
                api_key=api_key,
                timeout=settings.orchestrator_timeout,
                server_url=server_url,
                ollama_host=settings.ollama_host,
            )
            await orchestrator_llm.initialize()
            # 임베딩 기반 프로필 라우터 초기화
            embedding_router = EmbeddingRouter(embedding_provider)
            profile_dicts = [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": getattr(p, "description", ""),
                    "domain_scopes": p.domain_scopes,
                    "system_prompt": p.system_prompt,
                    "intent_hints": [
                        {"name": h.name, "patterns": h.patterns, "description": h.description}
                        for h in p.intent_hints
                    ],
                }
                for p in profiles
            ]
            await embedding_router.initialize(profile_dicts)

            orchestrator = MasterOrchestrator(
                llm=orchestrator_llm,
                profile_store=profile_store,
                session_memory=session_memory,
                workflow_engine=workflow_engine,
                tenant_service=tenant_service,
                embedding_router=embedding_router,
                access_policy=access_policy,
            )
            logger.info(
                "orchestrator_initialized",
                provider=settings.orchestrator_provider,
                model=settings.orchestrator_model,
            )
        else:
            logger.info("orchestrator_skipped", reason="API key 미설정")
    else:
        logger.info("orchestrator_disabled")

    providers = [embedding_provider, router_llm, main_llm, reranker, orchestrator_llm, kms_graph_client]

    # Task 009: Provider Registry + Router + Request Log + Response Cache
    provider_registry = provider_factory.build_registry()
    provider_router = ProviderRouter(
        registry=provider_registry,
        default_provider_id=main_llm.capability.provider_id,
    )

    # Async SQLAlchemy session factory (request_log + response_cache 에서 사용)
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    # asyncpg 드라이버 URL 보정
    db_url = settings.database_url
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    _engine = create_async_engine(db_url, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    request_log_service = RequestLogService(_session_factory)
    response_cache_service = ResponseCacheService(_session_factory)
    # Task 014: 30일 auto-purge sweeper 포함
    feedback_service = FeedbackService(_session_factory, retention_days=30)

    # Task 004: Saju Report Service + QueueWorker
    from src.services.saju_report_service import SajuReportService
    from src.infrastructure.job_queue import QueueWorker

    saju_report_service = SajuReportService(pool=pool, main_llm=main_llm)

    # Handler wrapper to inject job_id into payload
    async def saju_report_handler_wrapper(job_data: dict) -> dict:
        """QueueWorker에서 받은 job 데이터를 처리하고 job_id를 payload에 주입."""
        job_id = job_data["id"]
        payload = job_data["payload"]

        # payload에 job_id 추가
        payload["job_id"] = job_id

        return await saju_report_service.process_report_job(payload)

    # QueueWorker for saju-report queue
    saju_report_worker = QueueWorker(
        queue=job_queue,
        queue_name="saju-report",
        handler=saju_report_handler_wrapper,
        worker_id=f"saju-report-{getattr(settings, 'server_id', None) or 'default'}",
        poll_interval=2.0,
        max_concurrent=2,
    )

    logger.info(
        "gateway_integration_ready",
        providers=provider_registry.ids(),
    )

    return AppState(
        settings=settings,
        auth_service=auth_service,
        vector_store=vector_store,
        fact_store=fact_store,
        session_memory=session_memory,
        cache=cache,
        profile_store=profile_store,
        tool_registry=tool_registry,
        ai_router=ai_router,
        agent=agent,
        ingest_pipeline=ingest_pipeline,
        workflow_engine=workflow_engine,
        workflow_store=workflow_store,
        provider_factory=provider_factory,
        job_queue=job_queue,
        rate_limiter=rate_limiter,
        tenant_service=tenant_service,
        orchestrator=orchestrator,
        provider_registry=provider_registry,
        provider_router=provider_router,
        request_log_service=request_log_service,
        response_cache_service=response_cache_service,
        feedback_service=feedback_service,
        saju_report_service=saju_report_service,
        saju_report_worker=saju_report_worker,
        providers=providers,
    )


def start_cleanup_task(
    cache: PgCache,
    session_memory: SessionMemory,
    job_queue: JobQueue,
    interval: int,
) -> asyncio.Task:
    """만료 캐시/세션/stale 작업 주기적 정리 태스크를 시작한다."""

    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(interval)
            try:
                await cache.cleanup_expired()
                await session_memory.cleanup_expired()
                await job_queue.cleanup_stale(stale_seconds=600)
            except Exception as e:
                logger.warning("cleanup_failed", error=str(e))

    return asyncio.create_task(_periodic_cleanup())


async def seed_dev_api_keys(pool: Any) -> None:
    """개발용 API Key를 시드한다. ON CONFLICT DO NOTHING으로 멱등.

    api_keys 테이블이 없으면 (마이그레이션 미실행) 경고만 남기고 스킵.
    """
    async with pool.acquire() as conn:
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'api_keys'
            )
        """)
        if not table_exists:
            logger.warning("api_keys_table_missing", hint="alembic upgrade head 실행 필요")
            return

    dev_keys = [
        ("aip_dev_admin", "dev-admin-key", "dev-admin", "ADMIN", "SECRET", [], 120, "staff"),
        ("aip_dev_viewer", "dev-viewer-key", "dev-viewer", "VIEWER", "PUBLIC", [], 60, ""),
        ("aip_dev_editor", "dev-editor-key", "dev-editor", "EDITOR", "INTERNAL", [], 60, ""),
    ]
    async with pool.acquire() as conn:
        for raw_key, name, user_id, role, sec_lvl, profiles, rate, user_type in dev_keys:
            key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
            await conn.execute("""
                INSERT INTO api_keys (key_hash, name, user_id, user_role,
                                      security_level_max, allowed_profiles,
                                      rate_limit_per_min, user_type)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (key_hash) DO NOTHING
            """, key_hash, name, user_id, role, sec_lvl, profiles, rate, user_type)

        key_count = await conn.fetchval("SELECT COUNT(*) FROM api_keys WHERE is_active = TRUE")
        logger.info("api_keys_ready", active_keys=key_count)


async def shutdown(state: AppState) -> None:
    """앱 정리: 태스크 취소 + 커넥션 종료."""
    logger.info("shutdown_start")

    # Task 004: Saju Report Worker 정리
    if state.saju_report_worker:
        try:
            await state.saju_report_worker.stop()
        except Exception as e:
            logger.warning("saju_report_worker_stop_error", error=str(e))

    # Task 009/014: 신규 서비스 정리 (역순)
    if state.feedback_service:
        try:
            await state.feedback_service.stop_sweeper()
        except Exception as e:
            logger.warning("feedback_sweeper_stop_error", error=str(e))
    if state.response_cache_service:
        try:
            await state.response_cache_service.stop_sweeper()
        except Exception as e:
            logger.warning("cache_sweeper_stop_error", error=str(e))
    if state.request_log_service:
        try:
            await state.request_log_service.stop()
        except Exception as e:
            logger.warning("request_log_stop_error", error=str(e))

    if state.cleanup_task:
        state.cleanup_task.cancel()
        try:
            await state.cleanup_task
        except asyncio.CancelledError:
            pass

    for provider in state.providers:
        if provider and hasattr(provider, "close"):
            try:
                await provider.close()
            except Exception as e:
                logger.warning("provider_close_error", error=str(e))

    await state.vector_store.close()
    logger.info("shutdown_complete")
