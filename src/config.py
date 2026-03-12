"""AI Platform 설정 관리.

PostgreSQL only -- Redis 의존 없음.
모든 캐시/큐/세션을 PostgreSQL로 통합 관리.
"""

from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderMode(str, Enum):
    DEVELOPMENT = "development"  # Ollama + sentence-transformers (로컬)
    OPENAI = "openai"            # OpenAI API (GPT + text-embedding)
    PRODUCTION = "production"    # OpenAI (하위 호환)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AIP_",
        case_sensitive=False,
        extra="ignore",
    )

    # 프로바이더 모드
    provider_mode: ProviderMode = ProviderMode.DEVELOPMENT

    # PostgreSQL (유일한 인프라)
    database_url: str = "postgresql://aip:aip_dev@localhost:5434/ai_platform"

    # Ollama (development 모드)
    ollama_host: str = "http://localhost:11434"
    ollama_num_ctx: int = 16384

    # 응답 정책
    response_policy: str = "strict"

    # GPU 서버 URL (선택 -- 호스트에서 실행 시)
    embedding_server_url: str = ""
    reranker_server_url: str = ""
    router_llm_server_url: str = ""
    main_llm_server_url: str = ""

    # 모델 설정
    router_model: str = "qwen3:8b"
    main_model: str = "gemma2:9b"
    dev_embedding_model: str = "dragonkue/BGE-m3-ko"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    openai_api_key: str = ""
    prod_embedding_model: str = "text-embedding-3-small"
    prod_llm_model: str = "gpt-4o-mini"

    # 청킹
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # 임베딩 배치
    embed_batch_size: int = 64
    embed_max_batch_size: int = 128

    # 동시성
    max_concurrent_agents: int = 50
    pg_pool_min: int = 5
    pg_pool_max: int = 50
    embedding_concurrent_requests: int = 20

    # 작업 큐 (PostgreSQL SKIP LOCKED)
    job_poll_interval: float = 1.0       # 큐 폴링 간격(초)
    job_max_workers: int = 5             # 큐 워커 수
    job_max_retries: int = 3             # 최대 재시도
    job_retry_base_delay: int = 30       # 재시도 기본 지연(초)
    job_timeout: int = 300               # 개별 Job 타임아웃(초)
    job_shutdown_timeout: int = 30       # 셧다운 시 대기 시간(초)

    # 캐시 정리 주기 (초)
    cache_cleanup_interval: int = 300    # 5분마다 만료 캐시 삭제

    # JWT 인증 (선택)
    jwt_secret: str = ""

    # CORS (빈 리스트 = 모든 origin 허용, credentials 비활성)
    cors_origins: list[str] = []

    # 서버
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
