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

    # MLX GPU 서버 URL (development 모드 기본)
    embedding_server_url: str = ""
    reranker_server_url: str = ""
    router_llm_server_url: str = ""
    main_llm_server_url: str = ""

    # 응답 정책
    response_policy: str = "strict"

    # Ollama 폴백 (MLX 서버 미실행 시)
    ollama_host: str = "http://localhost:11434"
    ollama_num_ctx: int = 16384
    router_model: str = "qwen3:8b"
    main_model: str = "gemma2:9b"
    dev_embedding_model: str = "dragonkue/BGE-m3-ko"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    openai_api_key: str = ""
    prod_embedding_model: str = "text-embedding-3-small"
    prod_llm_model: str = "gpt-4o-mini"

    # 파서 (Vision Parser)
    parser_provider: str = "text"      # text | llamaparse
    llamaparse_api_key: str = ""
    parser_timeout: float = 120.0

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

    # 인증
    auth_required: bool = True
    jwt_secret: str = ""

    # KMS 연동 (도메인 SSOT)
    kms_api_url: str = ""              # 예: http://kms-api:3000/api
    kms_internal_key: str = ""         # INTERNAL_KEY 공유 비밀키
    kms_webhook_secret: str = ""       # Webhook HMAC-SHA256 비밀키

    # 오케스트레이터 (Master Router)
    orchestrator_model: str = "mlx-community/Qwen2.5-7B-Instruct-4bit"
    orchestrator_provider: str = "mlx"  # mlx | ollama | openai | anthropic
    orchestrator_server_url: str = ""  # MLX 서버 URL (미설정 시 router_llm_server_url 사용)
    orchestrator_api_key: str = ""  # 별도 API Key (미설정 시 openai_api_key 사용)
    orchestrator_enabled: bool = True  # 글로벌 킬 스위치
    orchestrator_timeout: float = 30.0  # 프로필 선택 타임아웃 (로컬 LLM은 느림)

    # CORS (빈 리스트 = 모든 origin 허용, credentials 비활성)
    cors_origins: list[str] = []

    # 서버
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
