"""AI Platform FastAPI 진입점.

Lifespan: DB풀, ProviderFactory, ProfileStore, ToolRegistry, Agent 초기화.
인프라 = PostgreSQL only (Redis 없음).
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.agent.profile_store import ProfileStore
from src.agent.universal import UniversalAgent
from src.config import settings
from src.gateway.router import gateway_router
from src.infrastructure.fact_store import FactStore
from src.infrastructure.memory.cache import PgCache
from src.infrastructure.memory.session import SessionMemory
from src.infrastructure.providers.factory import ProviderFactory
from src.infrastructure.vector_store import VectorStore
from src.pipeline.ingest import IngestPipeline
from src.router.ai_router import AIRouter
from src.safety.faithfulness import FaithfulnessGuard
from src.safety.pii_filter import PIIFilterGuard
from src.safety.response_policy import ResponsePolicyGuard
from src.tools.internal.fact_lookup import FactLookupTool
from src.tools.internal.rag_search import RAGSearchTool
from src.tools.registry import ToolRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 생명주기: 초기화 → 실행 → 정리."""
    logger.info("Starting AI Platform (mode: %s)", settings.provider_mode.value)

    # 1. VectorStore (PostgreSQL + pgvector)
    vector_store = VectorStore(settings.database_url)
    await vector_store.connect(
        min_size=settings.pg_pool_min,
        max_size=settings.pg_pool_max,
    )
    pool = vector_store.pool

    # 2. FactStore
    fact_store = FactStore(pool)

    # 3. Memory (PostgreSQL 기반)
    session_memory = SessionMemory(pool, default_ttl_seconds=3600)
    cache = PgCache(pool, default_ttl_seconds=300)

    # 4. Provider Factory
    provider_factory = ProviderFactory(settings)
    embedding_provider = provider_factory.get_embedding_provider()
    router_llm = provider_factory.get_router_llm()
    main_llm = provider_factory.get_main_llm()

    reranker = None
    try:
        reranker = provider_factory.get_reranker()
    except Exception as e:
        logger.warning("Reranker unavailable (degraded): %s", e)

    # 5. Profile Store
    profile_store = ProfileStore(pool, seed_dir="seeds/profiles")
    seed_count = await profile_store.load_seeds()
    logger.info("Loaded %d profile seeds", seed_count)

    # 6. Tool Registry
    tool_registry = ToolRegistry()
    tool_registry.register(RAGSearchTool(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        reranker=reranker,
    ))
    tool_registry.register(FactLookupTool(fact_store=fact_store))
    logger.info("Registered tools: %s", tool_registry.tool_names)

    # 7. AI Router
    ai_router = AIRouter(router_llm)

    # 8. Guardrails
    guardrails = {
        "faithfulness": FaithfulnessGuard(),
        "response_policy": ResponsePolicyGuard(),
        "pii_filter": PIIFilterGuard(),
    }

    # 9. Universal Agent
    agent = UniversalAgent(
        main_llm=main_llm,
        tool_registry=tool_registry,
        guardrails=guardrails,
    )

    # 10. Ingest Pipeline
    ingest_pipeline = IngestPipeline(
        vector_store=vector_store,
        embedding_provider=embedding_provider,
        settings=settings,
    )

    # 11. 주기적 정리 태스크 (만료 캐시/세션 삭제)
    async def periodic_cleanup():
        while True:
            await asyncio.sleep(settings.cache_cleanup_interval)
            try:
                await cache.cleanup_expired()
                await session_memory.cleanup_expired()
            except Exception as e:
                logger.warning("Periodic cleanup failed: %s", e)

    cleanup_task = asyncio.create_task(periodic_cleanup())

    # app.state에 컴포넌트 등록
    app.state.settings = settings
    app.state.vector_store = vector_store
    app.state.fact_store = fact_store
    app.state.session_memory = session_memory
    app.state.cache = cache
    app.state.profile_store = profile_store
    app.state.tool_registry = tool_registry
    app.state.ai_router = ai_router
    app.state.agent = agent
    app.state.ingest_pipeline = ingest_pipeline
    app.state.provider_factory = provider_factory

    logger.info("AI Platform ready")
    yield

    # 정리
    logger.info("Shutting down AI Platform")
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await vector_store.close()
    logger.info("AI Platform shutdown complete")


app = FastAPI(
    title="AI Platform",
    description="Universal Agent Platform - Profile 기반 도메인별 AI 에이전트",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(gateway_router, prefix="/api")
