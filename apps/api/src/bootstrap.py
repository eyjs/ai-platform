"""앱 부트스트랩: 컴포넌트 초기화 + 정리.

main.py lifespan에서 분리. 13개 컴포넌트 초기화를 단계별 함수로 정리.
"""

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))

from src.locale.bundle import LocaleBundle, set_locale
from src.agent.graph_executor import GraphExecutor
from src.agent.profile_store import ProfileStore
from src.config import Settings, fallback_backend_label
from src.gateway.access_policy import AccessPolicyStore
from src.gateway.auth import AuthService
from src.gateway.concurrency_gate import ConcurrencyGate
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
from src.router.semantic_classifier import SemanticClassifier
from src.safety.faithfulness import FaithfulnessGuard
from src.safety.pii_filter import PIIFilterGuard
from src.safety.response_policy import ResponsePolicyGuard
from src.tools.internal.fact_lookup import FactLookupTool
from src.tools.internal.rag_search import RAGSearchTool
from src.tools.registry import ToolRegistry
from src.services.tenant_service import TenantService
from src.services.kms_graph_client import KmsGraphClient
from src.services.null_kms_client import NullKmsClient
from src.supervisor.authz import DelegationAuthorizer
from src.supervisor.models import SupervisorLimits
from src.supervisor.planner_llm import SupervisorPlanner
from src.supervisor.sticky_guard import StickyGuardConfig
from src.supervisor.subagent_runner import SubAgentRunner
from src.supervisor.supervisor import Supervisor
from src.workflow.action_client import ActionClient
from src.workflow.checkpointer import build_checkpointer
from src.workflow.context_adapter import SajuContextAdapter
from src.workflow.engine import WorkflowEngine
from src.workflow.graph_builder import WorkflowGraphBuilder
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
    # 전역 동시 실행 게이트 (프로세스 단위). 레이트리밋(사용자별 속도)과 다른 축 —
    # 이쪽은 "지금 몇 개가 동시에 돌고 있나"의 절대 상한이다.
    concurrency_gate: ConcurrencyGate
    tenant_service: Optional[TenantService] = None
    # Task 002 (P0-2): Supervisor 합성 결과. 미배선/구성요소 부재 시 None(안전 폴백 — 엔트리 분기 미진입).
    supervisor: Optional[Supervisor] = None
    # classify-intent 라우트가 재사용하는 공유 분류기 (router_llm 기반)
    classifier: Optional[SemanticClassifier] = None

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
    # Fortune 해석 서비스
    fortune_service: Optional[Any] = None
    # Workflow action client (외부 API 호출)
    action_client: Optional[ActionClient] = None

    # T2: LangGraph AsyncPostgresSaver (미설치 시 None, T4가 엔진에 연결)
    # G1 ②안: thread_id 외부 노출 차단 — 어댑터(T4)만 접근
    workflow_checkpointer: Optional[Any] = None
    # context manager — lifespan 종료 시 __aexit__ 호출로 psycopg 풀 정리
    _workflow_checkpointer_cm: Optional[Any] = None

    # 내부 관리용
    cleanup_task: Optional[asyncio.Task] = None
    providers: list = field(default_factory=list)


async def _check_scope_tagging_alignment(pool, profiles) -> None:
    """프로필 domain_scopes 와 document_chunks 실태깅의 정합성을 검사한다 (WARN 전용).

    두 방향 모두 본다:
    - 스코프가 가리키는 도메인에 청크 0 → 그 프로필의 RAG 는 조용히 빈손이 된다.
    - 어떤 프로필도 스코프하지 않는 청크 도메인 → 스코프 프로필에선 검색 불가 데이터.
      (_common 은 전 프로필 자동 포함, __unplaced__ 는 의도된 미배치라 제외)
    """
    try:
        rows = await pool.fetch(
            "SELECT domain_code, count(*) AS cnt FROM document_chunks GROUP BY domain_code"
        )
    except Exception as e:  # noqa: BLE001 - 검사 실패가 부팅을 막으면 안 된다
        logger.warning("scope_tagging_check_failed", error=str(e))
        return

    tagged = {r["domain_code"]: r["cnt"] for r in rows}
    scoped_domains: set[str] = set()
    for p in profiles:
        for scope in p.domain_scopes:
            scoped_domains.add(scope)
            if scope not in tagged:
                logger.warning(
                    "profile_scope_has_no_chunks",
                    profile_id=p.id,
                    scope=scope,
                    hint="KMS 재편/매핑(seeds/domain_mapping.yaml) 확인 — 이 프로필 RAG 가 빈손이 된다",
                )

    for domain, cnt in tagged.items():
        if domain in ("_common", "__unplaced__") or domain in scoped_domains:
            continue
        logger.warning(
            "chunks_not_scoped_by_any_profile",
            domain_code=domain,
            chunks=cnt,
            hint="스코프 프로필에서 검색 불가 — 매핑 미정의(회사코드 fallback) 여부 확인",
        )


def _check_llm_wiring_alignment(settings) -> None:
    """설정값과 실제 LLM 배선의 정합성을 검사한다 (WARN 전용).

    DGX 위임(2026-07-16) 이후, 값은 env에 남아 있는데 아무도 읽지 않는 설정이 생겼다.
    설정이 살아 보이면 그걸 믿고 바꾼 사람이 "왜 아무 일도 안 일어나지"에서 시간을 태운다 —
    아키텍처 진단(2026-07-15)이 "죽은 설정"이라 부르며 재발 방지를 요구한 부류다.
    새 배선을 깔면서 같은 부류를 다시 만들었으므로, 부팅 때 스스로 실토하게 한다.

    거짓 경고를 내지 않는 게 중요하다 — 안 죽은 걸 죽었다고 하면 경고 전체가 무시된다.
    그래서 상용 퇴역(2026-07-16)과 함께 두 검사를 **지웠다**: AIP_MAIN_LLM_BACKEND 와
    AIP_PROVIDER_MODE 는 설정 자체가 사라져서, 계속 검사하면 없는 필드를 읽다 죽거나
    영영 안 울리는 죽은 검사가 된다. 죽은 설정을 잡는 검사가 죽은 검사가 되는 건 자기모순이다.
    (그 env 를 아직 compose/.env 에 남겨둔 경우는 pydantic extra="ignore" 가 조용히 무시한다.)
    """
    dgx_url = settings.dgx_llm_url

    if not dgx_url:
        # DGX 없이 DGX 설정만 남은 경우 (예: URL만 지우고 나머지를 방치)
        orphans = [
            name for name, value in (
                ("AIP_DGX_REPORT_MODEL", settings.dgx_report_model),
                ("AIP_DGX_ROUTER_MODEL", settings.dgx_router_model),
                ("AIP_DGX_ORCHESTRATOR_MODEL", settings.dgx_orchestrator_model),
                ("AIP_DGX_FORTUNE_MODEL", settings.dgx_fortune_model),
            ) if value
        ]
        if orphans:
            logger.warning(
                "dgx_settings_without_dgx_url",
                fields=orphans,
                hint="AIP_DGX_LLM_URL 미설정 — 이 값들은 읽히지 않는다",
            )
        return

    fallback_on = settings.dgx_local_fallback

    local_llm_urls = (
        ("AIP_MAIN_LLM_SERVER_URL", settings.main_llm_server_url),
        ("AIP_ROUTER_LLM_SERVER_URL", settings.router_llm_server_url),
        ("AIP_REPORT_LLM_SERVER_URL", settings.report_llm_server_url),
        ("AIP_FORTUNE_LLM_SERVER_URL", settings.fortune_llm_server_url),
        ("AIP_ORCHESTRATOR_SERVER_URL", settings.orchestrator_server_url),
    )

    if not fallback_on:
        unused = [name for name, url in local_llm_urls if url]
        if unused:
            logger.warning(
                "local_llm_urls_unused",
                fields=unused,
                hint="AIP_DGX_LOCAL_FALLBACK=false — 이 서버들은 폴백으로도 호출되지 않는다",
            )
        return

    # 폴백을 켰는데 로컬 URL이 없으면 폴백이 ollama_host(기본 localhost:11434)로 흘러,
    # 있지도 않은 서버를 부르며 "폴백이 있다"고 착각하게 된다 — 8104가 명목뿐이던 그 부류.
    if not settings.main_llm_server_url:
        logger.warning(
            "fallback_enabled_without_local_url",
            field="AIP_MAIN_LLM_SERVER_URL",
            ollama_host=settings.ollama_host,
            hint="폴백이 켜졌는데 로컬 MLX URL이 없다 — 폴백이 ollama_host로 흘러 명목뿐이 된다",
        )


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
        rls_enabled=settings.rls_enabled,
        rls_role=settings.rls_role,
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
    # D17: RS256 공개키 로드. 경로가 설정됐는데 읽기 실패면 즉시 중단 —
    # 보안 설정 오류를 조용한 HS256 강등으로 흡수하지 않는다.
    jwt_public_key = ""
    if settings.jwt_public_key_path:
        from pathlib import Path as _Path
        jwt_public_key = _Path(settings.jwt_public_key_path).read_text()

    auth_service = AuthService(
        pool=pool,
        jwt_secret=settings.jwt_secret,
        auth_required=settings.auth_required,
        access_policy=access_policy,
        profile_auth_strict=settings.profile_auth_strict,
        publishable_rate_limit_max=settings.publishable_rate_limit_max,
        jwt_public_key=jwt_public_key,
        jwt_hs256_fallback=settings.jwt_hs256_fallback,
    )
    logger.info(
        "auth_initialized",
        auth_required=settings.auth_required,
        profile_auth_strict=settings.profile_auth_strict,
        jwt_rs256_enabled=bool(jwt_public_key),
        jwt_hs256_fallback=settings.jwt_hs256_fallback,
    )

    # 3. FactStore + Memory
    fact_store = FactStore(pool)
    session_memory = SessionMemory(pool, default_ttl_seconds=3600)
    cache = PgCache(pool, default_ttl_seconds=300)

    # 4. Provider Factory
    # 배선 직전에 설정 정합성부터 실토시킨다 — 아래 get_*_llm 로그와 나란히 찍혀야
    # "설정은 이런데 실제 배선은 저렇다"가 한눈에 대조된다.
    _check_llm_wiring_alignment(settings)

    async def _load_dgx_catalog(factory: "ProviderFactory", s) -> None:
        """DGX 가 실제 서빙하는 모델 태그를 팩토리에 주입한다.

        프로필의 main_model 을 DGX 주 경로에 태울지 판단하는 유일한 근거다. 이게 없으면
        팩토리는 프로필 모델을 무시하고 기본 DGX 모델만 쓴다(안전한 기존 동작).

        조회 실패를 치명으로 보지 않는다 — DGX 가 잠깐 안 잡혀도 부팅은 되어야 하고,
        카탈로그를 모르면 팩토리가 알아서 보수적으로 동작한다.
        """
        if not s.dgx_llm_url:
            return
        # 함수 안에서 import 한다 — 이 감싸는 함수 안에 이미 지역 `import httpx` 가 있어
        # httpx 가 함수 스코프 지역명이 되고, 모듈 레벨 import 를 참조하면
        # "cannot access free variable 'httpx'" 로 죽는다(실측).
        import httpx

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.get(f"{s.dgx_llm_url.rstrip('/')}/api/tags")
                res.raise_for_status()
                names = [m.get("name", "") for m in res.json().get("models", [])]
            factory.set_dgx_catalog(names)
            logger.info("dgx_catalog_loaded", count=len(names), models=names)
        except Exception as e:
            logger.warning(
                "dgx_catalog_load_failed",
                error=str(e),
                effect="프로필 main_model 은 무시되고 dgx_main_model 로만 서빙한다",
            )

    provider_factory = ProviderFactory(settings)
    await _load_dgx_catalog(provider_factory, settings)
    embedding_provider = provider_factory.get_embedding_provider()
    router_llm = provider_factory.get_router_llm()               # 분류/라우팅(1.7B 등)
    orchestration_llm = provider_factory.get_orchestration_llm()  # 계획·재작성·확장(4B 등)
    main_llm = provider_factory.get_main_llm()                    # 생성(9B+)
    # 리포트 전용 LLM(report_llm_server_url 미설정 시 main_llm). 채팅=9B / 리포트=14B 분리.
    report_llm = provider_factory.get_report_llm()
    logger.info(
        "providers_initialized",
        embedding=type(embedding_provider).__name__,
        router_llm=type(router_llm).__name__,
        orchestration_llm=type(orchestration_llm).__name__,
        main_llm=type(main_llm).__name__,
        report_llm=type(report_llm).__name__,
    )

    reranker = None
    try:
        reranker = provider_factory.get_reranker()
        logger.info("reranker_initialized", type=type(reranker).__name__)
    except Exception as e:
        logger.warning("reranker_unavailable", error=str(e))

    # 콜드스타트 워밍업: 임베더/리랭커 모델을 기동 시 1회 예열해 첫 질의 지연(~20s)을 제거한다.
    # 모델 서버가 첫 추론에서 모델을 lazy-load 하므로, 실제 사용자 질의 전에 미리 로드시킨다.
    # 실패해도 기동을 막지 않는다(모델 서버 미가동 시 첫 질의에서 자연 로드로 폴백).
    try:
        _warm_t = time.time()
        await embedding_provider.embed_batch(["워밍업"])
        if reranker is not None:
            await reranker.rerank("워밍업", ["워밍업 문서"], top_k=1)
        logger.info("model_warmup_complete", ms=round((time.time() - _warm_t) * 1000, 1))
    except Exception as e:
        logger.warning("model_warmup_failed", error=str(e))

    # 5. Profile Store
    profile_store = ProfileStore(pool, seed_dir="seeds/profiles")
    seed_count = await profile_store.load_seeds()
    profiles = await profile_store.list_all()
    logger.info("profiles_loaded", seed_count=seed_count)

    # 프로필 간 intent_hints 패턴 중복 검사
    _check_profile_pattern_overlap(profiles)

    # 프로필 스코프 ↔ 실제 청크 태깅 정합성 검사 (fail-loud, 2026-07 실사고 재발 방지):
    # KMS 재편·매핑 누락으로 스코프가 가리키는 도메인에 청크가 0이면 해당 프로필의
    # RAG 가 조용히 전멸한다(final 0). 부팅 시점에 시끄럽게 드러낸다(치명 아님 — WARN).
    await _check_scope_tagging_alignment(pool, profiles)

    # 6. Tool Registry
    tool_registry = ToolRegistry()
    tool_registry.register(RAGSearchTool(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        reranker=reranker,
        # 쿼리 확장은 생성적 오케스트레이션 → 4B orchestration_llm (분류용 router_llm 아님)
        router_llm=orchestration_llm,
        # 무관 검색(리랭커 절대점수 하한 미달) → 빈 결과 → 정직 반려(환각 방지)
        min_rerank_score=settings.rag_min_rerank_score,
    ))
    tool_registry.register(FactLookupTool(fact_store=fact_store))
    from src.tools.internal import SajuLookupTool, SajuReportPaperTool, SajuReportCompatibilityTool
    tool_registry.register(SajuLookupTool(backend_url=settings.saju_backend_url))
    tool_registry.register(SajuReportPaperTool(llm_provider=report_llm))
    tool_registry.register(SajuReportCompatibilityTool(llm_provider=report_llm))

    # FlowSNS 연동 도구
    if settings.flowsns_api_key:
        from src.tools.internal.flowsns import (
            FlowSNSClient,
            FlowSNSTasksTool,
            FlowSNSClientsTool,
            FlowSNSAccountsTool,
            FlowSNSDashboardTool,
            FlowSNSCalendarTool,
            FlowSNSTaskActionsTool,
            FlowSNSApprovalTool,
            FlowSNSNotificationsTool,
            FlowSNSReportsTool,
            FlowSNSProfilesTool,
        )
        flowsns_client = FlowSNSClient(
            base_url=settings.flowsns_api_url,
            api_key=settings.flowsns_api_key,
            timeout=settings.flowsns_timeout,
        )
        for tool_cls in (
            FlowSNSTasksTool,
            FlowSNSClientsTool,
            FlowSNSAccountsTool,
            FlowSNSDashboardTool,
            FlowSNSCalendarTool,
            FlowSNSTaskActionsTool,
            FlowSNSApprovalTool,
            FlowSNSNotificationsTool,
            FlowSNSReportsTool,
            FlowSNSProfilesTool,
        ):
            tool_registry.register(tool_cls(client=flowsns_client))
        logger.info("flowsns_tools_registered", count=10, api_url=settings.flowsns_api_url)
    else:
        logger.info("flowsns_tools_skipped", reason="FLOWSNS_API_KEY not configured")

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
        # 자동감지는 로컬 MLX 서버에 "지금 뜬 모델명"을 묻는 것이라 로컬 경로를 쓸 때만
        # 의미가 있다. DGX 단독(폴백 off)이면 model_name을 아무도 안 쓰므로 생략하고,
        # 폴백이 켜져 있으면 그 이름이 폴백 chat model로 들어가니 반드시 감지해야 한다.
        needs_local_model_name = not settings.dgx_llm_url or settings.dgx_local_fallback
        if settings.main_llm_server_url and needs_local_model_name:
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

        chat_model = provider_factory.get_chat_model(model_name=chat_model_name)
        logger.info("chat_model_initialized", type=type(chat_model).__name__)
    except ImportError:
        logger.warning("chat_model_unavailable, agentic mode disabled")

    # 10. Workflow Engine (Agent보다 먼저 — Agent가 의존)
    workflow_store = WorkflowStore(pool=pool, seed_dir="seeds/workflows")
    await workflow_store.load_seeds()
    action_client = ActionClient()
    # 공유 분류기 — WorkflowEngine과 classify-intent 라우트가 동일 인스턴스 재사용.
    classifier = SemanticClassifier(router_llm)

    # T2: AsyncPostgresSaver 체크포인터 부트스트랩 (psycopg v3 전용 풀, asyncpg와 별도).
    # 미설치 환경에서는 (None, None) 반환 — 엔진이 MemorySaver로 자동 폴백.
    workflow_checkpointer, _workflow_checkpointer_cm = await build_checkpointer(
        settings.database_url
    )
    if workflow_checkpointer is None:
        logger.warning(
            "workflow_no_checkpointer",
            reason="AsyncPostgresSaver 미설치 — MemorySaver 자동 폴백 (단위 테스트 모드)",
        )

    _context_adapters = {
        # 서비스별 dynamic 스텝 enrichment 플러그인. 프로파일이 이름으로 선택.
        "saju": SajuContextAdapter(backend_url=settings.saju_backend_url),
    }
    _graph_builder = WorkflowGraphBuilder(
        store=workflow_store,
        llm=main_llm,
        context_adapters=_context_adapters,
        classifier=classifier,
        action_client=action_client,
    )
    logger.info("workflow_graph_builder_initialized", backend="langgraph")

    workflow_engine = WorkflowEngine(
        workflow_store,
        action_client=action_client,
        llm=main_llm,  # dynamic 스텝(캐릭터 통찰)용
        context_adapters=_context_adapters,
        # select 자유입력 분기를 의미로 판단하는 공통 분류기(경량 router_llm).
        classifier=classifier,
        # LangGraph 경로 의존성 — checkpointer=None이면 엔진이 MemorySaver로 자동 폴백
        graph_builder=_graph_builder,
        checkpointer=workflow_checkpointer,
    )
    logger.info("workflows_loaded", count=workflow_store.count, backend="langgraph")

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
        # P0-2/3 model wiring: profile.main_model alias 해석을 위해 주입.
        # chat_model(기본) 은 그대로 유지되며, resolved alias 가 있을 때만 override 빌드.
        provider_factory=provider_factory,
        settings=settings,
        # 라이트사이징: 계획수립·쿼리재작성은 orchestration_llm(4B 등)으로 (생성만 main_llm)
        orchestration_llm=orchestration_llm,
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

    # 13-1. 전역 동시 실행 게이트 — max_concurrent_agents를 실제 상한으로 배선한다.
    # 이 값은 2026-07-15 진단까지 소비처가 없어 죽은 설정이었다("50까지 받는다"는
    # 착시). 프로세스 단위 상한임을 로그에 남겨 워커 증설 시 곱셈을 놓치지 않게 한다.
    concurrency_gate = ConcurrencyGate(settings.max_concurrent_agents)
    logger.info(
        "concurrency_gate_initialized",
        limit=settings.max_concurrent_agents,
        scope="per-process",
    )

    # 14. TenantService
    tenant_service = TenantService(pool)

    # 14-B. Supervisor 합성 (task-002, P0-2). agent/ai_router/profile_store/tool_registry/
    # tenant_service/access_policy/orchestration_llm이 모두 준비된 이후에만 합성한다.
    # orchestration_llm(경량 4B) 우선, 없으면 main_llm으로 폴백(부트스트랩 다른 곳과 동일 관례).
    supervisor_runner = SubAgentRunner(profile_store, ai_router, agent, tool_registry)
    supervisor_authorizer = DelegationAuthorizer(profile_store, tenant_service, access_policy, settings)
    # decompose=경량(4B), synthesize=대형(main 9B) — "생성은 큰 LLM" (실사고: 4B 종합이
    # 중국어 혼입·부정문 뒤집힘 유발). workflow_engine은 sticky 감지용(멀티턴 위임 연속성).
    supervisor_planner = SupervisorPlanner(orchestration_llm or main_llm, synthesize_llm=main_llm)
    supervisor_limits = SupervisorLimits(
        # P1-1/P1-4는 opt-in — 켜면 턴당 LLM 호출이 추가된다(라이트사이징 원칙과 트레이드오프).
        adaptive_replan=settings.supervisor_adaptive_replan,
        max_replan_rounds=settings.supervisor_max_replan_rounds,
        review_gate=settings.supervisor_review_gate,
        # Phase 3: 단일 위임 passthrough(라우팅 파리티 — 자동 라우팅은 supervisor 전담).
        single_passthrough=settings.supervisor_single_passthrough,
    )
    # V1 sticky 이중 가드 — 방치 세션(TTL)과 타도메인 하이재킹을 걸러낸다.
    # 임베딩은 이미 만들어둔 provider를 재사용한다(질문·프로필 신호 유사도용).
    supervisor_sticky_guard = StickyGuardConfig(
        ttl_seconds=settings.sticky_ttl_seconds,
        break_similarity=settings.sticky_break_similarity,
        break_margin=settings.sticky_break_margin,
    )
    supervisor = Supervisor(
        supervisor_planner, supervisor_runner, supervisor_authorizer, supervisor_limits, profile_store,
        workflow_engine=workflow_engine,
        sticky_guard=supervisor_sticky_guard,
        embedding_provider=embedding_provider,
    )
    logger.info(
        "supervisor_initialized",
        profile_id=settings.supervisor_profile_id,
        adaptive_replan=settings.supervisor_adaptive_replan,
        review_gate=settings.supervisor_review_gate,
        sticky_ttl_seconds=settings.sticky_ttl_seconds,
        sticky_break_similarity=settings.sticky_break_similarity,
    )

    # 15. (제거됨) 레거시 MasterOrchestrator — Phase 3 컷오버로 자동 라우팅은 supervisor가
    # 전담한다(라우팅 = 1위임의 특수케이스). 롤백이 필요하면 git 히스토리의
    # src/orchestrator/ + 이 섹션을 복원할 것.

    providers = [embedding_provider, router_llm, main_llm, reranker, kms_graph_client]

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
    _engine = create_async_engine(
        db_url,
        pool_pre_ping=True,
        pool_size=settings.sa_pool_size,
        max_overflow=settings.sa_pool_max_overflow,
        pool_timeout=30,
        pool_recycle=3600,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)

    logger.info(
        "db_pools_configured",
        asyncpg_min=settings.pg_pool_min,
        asyncpg_max=settings.pg_pool_max,
        sa_pool_size=settings.sa_pool_size,
        sa_max_overflow=settings.sa_pool_max_overflow,
    )
    request_log_service = RequestLogService(_session_factory)
    response_cache_service = ResponseCacheService(_session_factory)
    # Task 014: 30일 auto-purge sweeper 포함
    feedback_service = FeedbackService(_session_factory, retention_days=30)

    # Fortune 해석 서비스 (동기 — DB 불필요)
    # 무료·패턴 콘텐츠($0)는 로컬 LLM(MLX)로 라우팅, 고난도는 main_llm(상용).
    from src.services.consumers.saju.fortune_service import FortuneService
    fortune_service = FortuneService(main_llm=main_llm, local_llm=provider_factory.get_local_llm())
    logger.info("fortune_service_initialized")

    # Task 004: Saju Report Service + QueueWorker
    from src.services.consumers.saju.saju_report_service import SajuReportService
    from src.infrastructure.job_queue import QueueWorker

    # 리포트는 report_llm(14B)로 — 9B JSON 반복붕괴 회피. 채팅은 main_llm(9B) 유지.
    saju_report_service = SajuReportService(pool=pool, main_llm=report_llm)

    # QueueWorker for saju-report queue
    saju_report_worker = QueueWorker(
        queue=job_queue,
        queue_name="saju-report",
        handler=saju_report_service.process_report_job,
        worker_id=f"saju-report-{getattr(settings, 'server_id', None) or 'default'}",
        poll_interval=2.0,
        max_concurrent=4,  # 유료 리포트 동시 처리 — DGX 단일 서빙이 감당할 상한(상용 클라우드 아님)
    )

    logger.info(
        "gateway_integration_ready",
        providers=provider_registry.ids(),
    )

    # Profile YAML Hot Reload 시작
    profile_store.start_watcher()

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
        concurrency_gate=concurrency_gate,
        tenant_service=tenant_service,
        supervisor=supervisor,
        provider_registry=provider_registry,
        provider_router=provider_router,
        request_log_service=request_log_service,
        response_cache_service=response_cache_service,
        feedback_service=feedback_service,
        fortune_service=fortune_service,
        saju_report_service=saju_report_service,
        saju_report_worker=saju_report_worker,
        action_client=action_client,
        workflow_checkpointer=workflow_checkpointer,
        _workflow_checkpointer_cm=_workflow_checkpointer_cm,
        providers=providers,
        classifier=classifier,
    )


def start_cleanup_task(
    cache: PgCache,
    session_memory: SessionMemory,
    job_queue: JobQueue,
    interval: int,
    rate_limiter: PGRateLimiter | None = None,
    rate_limit_idle_ttl: int = 3600,
) -> asyncio.Task:
    """만료 캐시/세션/stale 작업 주기적 정리 태스크를 시작한다."""

    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(interval)
            try:
                await cache.cleanup_expired()
                await session_memory.cleanup_expired()
                await job_queue.cleanup_stale(stale_seconds=600)
                if rate_limiter:
                    await rate_limiter.cleanup_stale(idle_seconds=rate_limit_idle_ttl)
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

    if state.action_client:
        try:
            await state.action_client.close()
        except Exception as e:
            logger.warning("action_client_close_error", error=str(e))

    # T2: AsyncPostgresSaver psycopg 풀 정리 (context manager __aexit__)
    if state._workflow_checkpointer_cm:
        try:
            await state._workflow_checkpointer_cm.__aexit__(None, None, None)
            logger.info("checkpointer_closed")
        except Exception as e:
            logger.warning("checkpointer_close_error", error=str(e))

    await state.vector_store.close()
    logger.info("shutdown_complete")
