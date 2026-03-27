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
from src.gateway.auth import AuthService
from src.gateway.rate_limiter import PGRateLimiter
from src.infrastructure.fact_store import FactStore
from src.infrastructure.job_queue import JobQueue
from src.infrastructure.memory.cache import PgCache
from src.infrastructure.memory.session import SessionMemory
from src.infrastructure.providers.factory import ProviderFactory
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
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

    # 2. AuthService
    auth_service = AuthService(
        pool=pool,
        jwt_secret=settings.jwt_secret,
        auth_required=settings.auth_required,
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
                resp = httpx.get(
                    f"{settings.main_llm_server_url}/v1/models", timeout=5.0,
                )
                if resp.status_code == 200:
                    models = resp.json().get("data", [])
                    if models:
                        chat_model_name = models[0]["id"]
                        logger.info("chat_model_auto_detected", model=chat_model_name)
            except Exception:
                pass  # 감지 실패 시 기본 모델명 사용

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
        ("aip_dev_admin", "dev-admin-key", "dev-admin", "ADMIN", "SECRET", [], 120),
        ("aip_dev_viewer", "dev-viewer-key", "dev-viewer", "VIEWER", "PUBLIC", [], 60),
        ("aip_dev_editor", "dev-editor-key", "dev-editor", "EDITOR", "INTERNAL", [], 60),
    ]
    async with pool.acquire() as conn:
        for raw_key, name, user_id, role, sec_lvl, profiles, rate in dev_keys:
            key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
            await conn.execute("""
                INSERT INTO api_keys (key_hash, name, user_id, user_role,
                                      security_level_max, allowed_profiles, rate_limit_per_min)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (key_hash) DO NOTHING
            """, key_hash, name, user_id, role, sec_lvl, profiles, rate)

        key_count = await conn.fetchval("SELECT COUNT(*) FROM api_keys WHERE is_active = TRUE")
        logger.info("api_keys_ready", active_keys=key_count)


async def shutdown(state: AppState) -> None:
    """앱 정리: 태스크 취소 + 커넥션 종료."""
    logger.info("shutdown_start")

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
