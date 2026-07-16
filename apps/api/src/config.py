"""AI Platform 설정 관리.

PostgreSQL only -- Redis 의존 없음.
모든 캐시/큐/세션을 PostgreSQL로 통합 관리.
"""

from enum import Enum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 위치를 실행 디렉토리와 무관하게 탐색 (config.py 기준 상위 경로들).
# 로컬: apps/api/src/config.py → 모노레포 루트(.env) + apps/api(.env).
# 도커: /app/src/config.py → 상위가 얕아 후보가 없을 수 있음(그땐 OS 환경변수 사용).
# OS 환경변수(docker-compose 등)는 pydantic 우선순위상 .env 보다 항상 우선한다.
def _candidate_env_files() -> tuple[str, ...]:
    # 고정 절대경로만 사용한다. CWD 상대 ".env"는 의도적으로 제외 —
    # 임의 작업디렉토리에 심어진 악성 .env가 정규 설정을 덮어쓰는 것을 막는다.
    # 도커처럼 경로가 얕아 후보가 없을 땐 OS 환경변수(compose)가 설정을 제공한다.
    here = Path(__file__).resolve()
    paths: list[str] = []
    for up in (3, 1):  # parents[3]=모노레포 루트(로컬), parents[1]=apps/api
        if up < len(here.parents):
            paths.append(str(here.parents[up] / ".env"))
    return tuple(paths)


_ENV_FILES = _candidate_env_files()


class ProviderMode(str, Enum):
    DEVELOPMENT = "development"  # Ollama + sentence-transformers (로컬)
    OPENAI = "openai"            # OpenAI API (GPT + text-embedding)
    PRODUCTION = "production"    # OpenAI (하위 호환)
    ANTHROPIC = "anthropic"      # Anthropic Claude (메인/라우터 LLM) + 로컬/OpenAI 임베딩


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
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
    # 무료 콘텐츠 전용 로컬 MLX(8106=Qwen3.5-9B). main_llm과 분리 — 챗 모델 자동감지 오염 방지.
    fortune_llm_server_url: str = ""
    # 사주 리포트 전용 LLM 서버. 미설정 시 main_llm으로 폴백.
    # 채팅은 빠른 9B(main), 리포트는 JSON 안정적인 14B로 분리하기 위함(8104).
    report_llm_server_url: str = ""

    # 응답 정책
    response_policy: str = "strict"

    # Ollama 폴백 (MLX 서버 미실행 시)
    ollama_host: str = "http://localhost:11434"
    ollama_num_ctx: int = 16384
    router_model: str = "qwen3.5:9b"
    main_model: str = "qwen3.5:27b"
    dev_embedding_model: str = "dragonkue/BGE-m3-ko"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    # RAG 관련도 하한(리랭커 절대점수). bge-reranker-v2-m3는 무관 청크를 sigmoid
    # 0.5(logit≈0)에 못박고 관련 청크만 그 위로 올린다. fused 점수는 벡터 min-max
    # 정규화 때문에 무관 청크도 tier를 통과하므로(0.7*0.5+0.3*1.0=0.65), 리랭커
    # 절대점수가 이 값 미만인 청크는 컨텍스트에서 제외한다. 전부 미달이면 RAG가
    # RAG 관련도 하한의 "전역 기본값" — 프로필이 rag_min_rerank_score를 지정하지
    # 않았을 때만 적용된다(도메인별 캘리브레이션은 Profile YAML이 담당).
    # 여기 값은 도메인 무관 "순수 노이즈 바닥"으로 보수적으로 둔다: bge-reranker는
    # 무관 청크를 sigmoid 0.5(logit≈0)에 못박으므로 0.505면 순수 노이즈(주식·날씨
    # 등 0.500)만 걸러 미보정 신규 도메인이 과반려되지 않는다. 도메인이 붙으면
    # 각 프로필이 probe_rerank_floor.py로 자체 floor를 정해 오버라이드(예: 보험 0.58).
    rag_min_rerank_score: float = 0.505
    openai_api_key: str = ""
    prod_embedding_model: str = "text-embedding-3-small"
    prod_llm_model: str = "gpt-4o-mini"
    # Anthropic Claude (provider_mode=anthropic). 기본은 비용 최적 Haiku.
    anthropic_api_key: str = ""
    anthropic_main_model: str = "claude-haiku-4-5"
    anthropic_router_model: str = "claude-haiku-4-5"

    # 파서 (Vision Parser)
    parser_provider: str = "text"      # text | llamaparse | engine
    llamaparse_api_key: str = ""
    parser_timeout: float = 120.0

    # DocForge 파싱 서비스 (parser_provider=engine 일 때)
    docforge_url: str = "http://localhost:5001"
    docforge_timeout_sec: float = 300.0       # 개별 HTTP 요청(제출/폴링) 타임아웃
    # 비동기 파싱 잡 완료까지 총 대기 한도 (대형 약관 ~1500p 대응). docforge가
    # 큐에서 하나씩 처리하므로 이 시간 동안 짧은 폴링으로 대기한다.
    docforge_max_wait_sec: float = 5400.0
    docforge_internal_key: str = ""

    # 청킹
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # 임베딩 배치
    embed_batch_size: int = 64
    embed_max_batch_size: int = 64            # 대형 문서도 배치당 최대 64 (서버 부하 완화)
    # 임베딩 서버로의 동시 배치 요청 상한. 대형 문서(수천 청크)에서 모든 배치를
    # 동시에 발사하면 단일 임베딩 서버가 교착(CLOSE_WAIT)되므로 제한한다.
    # 로컬 MLX 단일 서버 기준 2가 안전 상한 — 3이상이면 배치당 지연이 타임아웃을
    # 넘겨 ReadTimeout→서킷 오픈 연쇄로 대형 문서 적재가 실패한다(실사고).
    embed_concurrency: int = 2

    # 임베딩 프로바이더
    # 64텍스트 배치가 로컬 MLX 서버에서 부하 시 30초를 초과한다(실사고:
    # 1,599청크 문서 25배치 중 후반 배치 ReadTimeout 연쇄) — 여유 있게 잡고
    # 진짜 장애는 connect_timeout 과 서킷브레이커가 fast-fail 로 잡는다.
    embedding_timeout: float = 120.0          # HTTP 읽기 타임아웃 (초)
    embedding_connect_timeout: float = 5.0    # 커넥션 타임아웃 (초)

    # 동시성
    max_concurrent_agents: int = 50
    pg_pool_min: int = 2
    pg_pool_max: int = 20
    sa_pool_size: int = 5
    sa_pool_max_overflow: int = 10
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
    # 내부 링크(KMS·DocForge) 상시 연결 감시 주기 (초). 0이면 부팅 1회만 점검
    link_check_interval: int = 60
    # 레이트리밋 유휴 버킷 정리 TTL (초). 이만큼 미사용 버킷은 만석 상태이므로 삭제 안전 (B5)
    rate_limit_idle_ttl: int = 3600

    # 인증
    auth_required: bool = True
    jwt_secret: str = ""
    # JWT 비대칭 검증 (D17, Step 13). RSA 공개키 PEM 파일 경로.
    # 설정 시 RS256 토큰(bff 개인키 서명) 검증 활성. api는 공개키만 가진다.
    jwt_public_key_path: str = ""
    # 과도기 HS256 허용 (D17). RS256 전환 검증 후 false로 잠가 대칭키를 퇴역시킨다.
    jwt_hs256_fallback: bool = True
    # 프로필 인가 deny-by-default (A1). True면 빈 allowed_profiles/테넌트 매핑 = 전체 거부.
    # 와일드카드 "*"로 명시적 전체 허용. 기존 fail-open 호환을 위해 기본 False.
    profile_auth_strict: bool = False
    # publishable(위젯) 키 분당 쿼터 상한 (B4). 발급 시 이 값을 초과하면 거부.
    publishable_rate_limit_max: int = 120
    # 테넌트 격리 기본 테넌트 (A2/4a). tenant_id 미지정 쓰기는 이 값으로 스탬핑.
    # 마이그레이션 019의 백필 값과 일치해야 한다.
    default_tenant_id: str = "default"
    # RLS 심층방어 (A2/4c). true면 요청마다 SET ROLE rls_role + GUC로 DB가 테넌트 강제.
    # 기본 false(superuser 접속 = RLS 우회). 운영 전환 전 마이그레이션 020 적용 필요.
    rls_enabled: bool = False
    rls_role: str = "aip_app"

    # KMS 연동 (도메인 SSOT)
    kms_api_url: str = ""              # 예: http://kms-api:3000/api
    kms_internal_key: str = ""         # INTERNAL_KEY 공유 비밀키
    kms_webhook_secret: str = ""       # Webhook HMAC-SHA256 비밀키

    # 오케스트레이션 LLM (계획수립·쿼리재작성·supervisor decompose — 라이트사이징 중경량 트랙)
    # 이름의 orchestrator_는 역사적 잔재(레거시 MasterOrchestrator와 공유하던 키). env 호환 유지.
    orchestrator_model: str = "mlx-community/Qwen3.5-9B-4bit"
    orchestrator_provider: str = "mlx"  # mlx | ollama | openai | anthropic
    orchestrator_server_url: str = ""  # MLX 서버 URL (미설정 시 router_llm_server_url 사용)

    # CORS (빈 리스트 = 모든 origin 허용, credentials 비활성)
    # 개발 환경: 웹앱(localhost:3000) + BFF(localhost:3001) 기본 허용
    # 프로덕션: Vercel 배포 도메인 추가
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://ai-platform-eight-sigma.vercel.app",
    ]

    # LLM 응답 최대 토큰 (MLX 기본 512 방지)
    llm_max_tokens: int = 4096

    # 로케일
    locale: str = "ko"
    llm_system_prefix: str = ""  # 빈값 = 로케일 기본값 사용
    fallback_profile_id: str = "general-chat"
    # Supervisor 엔트리 감지 키(task-002). chatbot_id가 이 값과 일치하면
    # gateway/routes/chat.py가 일반 그래프 실행을 건너뛰고 state.supervisor.supervise()로 분기한다.
    supervisor_profile_id: str = "supervisor"
    # P1-1 adaptive replan: 위임 라운드 완료 후 메인이 부족한 도메인을 재위임할지 판단.
    # 턴당 orchestration LLM 호출 1회가 추가되고 과위임(오라우팅) 위험이 있어 opt-in.
    supervisor_adaptive_replan: bool = False
    supervisor_max_replan_rounds: int = 1
    # P1-4 메인 검토 게이트: 서브 답변을 메인이 판정(pass/fail) 후 통과분만 종합.
    # 서브 결과당 LLM 호출 1회가 추가되어 opt-in. 재생성 아님 — 판정만 한다.
    supervisor_review_gate: bool = False
    # Phase 3 컷오버: 자동 라우팅(chatbot_id 미지정)은 supervisor가 전담한다.
    # (레거시 MasterOrchestrator와 orchestrator_backend 스위치는 제거됨 — 롤백은 git 히스토리)
    # Phase 3: 단일 위임이 성공하면 synthesize를 건너뛰고 서브 답변을 그대로 전달.
    # 자동 라우팅 파리티(레거시는 선택된 프로파일 답변을 그대로 반환) + 지연 절감.
    # 켜면 단일 위임 응답이 "메인 종합 문체"가 아닌 서브 원문으로 나간다.
    supervisor_single_passthrough: bool = False

    # ── V1 sticky 이중 가드 (진단 2026-07-15: "sticky 워크플로우 하이재킹") ──
    # detect_sticky는 미완료 워크플로우가 있으면 무조건 그리로 잡아, 방치된 사주
    # 워크플로우가 "자동차보험 절차" 질문까지 삼켰다. 아래 두 가드로 좁힌다.
    #
    # ① TTL: 세션이 시작된 지 이만큼 지났으면 방치로 보고 sticky를 놓는다.
    # started_at(시작 시각) 기준 — 마지막 활동 시각을 워크플로우가 노출하지 않는다.
    # 그래서 "진행 중이지만 오래 걸리는" 세션도 만료될 수 있어 넉넉히 잡는다.
    # 하이재킹의 주 방어선은 ②이고 이건 방치 세션을 걷어내는 보조 그물이다.
    sticky_ttl_seconds: int = 7200  # 2시간
    # ② 비대칭 관련성 가드 — **기본은 sticky 유지, 강한 반증이 있을 때만 깬다.**
    # 진단서 문구("유사도 ≥ 임계일 때만 sticky")를 그대로 쓰면 워크플로우 중간 발화
    # ("1990-05-15", "투자")가 전부 떨어져 나간다. 그런 입력은 도메인 신호가 약해
    # 유사도가 낮은 게 정상이고, decompose가 단독 질문으로 재해석하면 무의미한 답이
    # 나온다(graph.py sticky_delegate 주석의 실사고). 그래서 방향을 뒤집었다:
    # 다른 도메인이 sticky보다 마진만큼 앞설 때만 깬다. 신호가 약하면 유지 — 보존이 기본값.
    #
    # ★수치는 실측 보정(2026-07-16, 14문항 배터리 · BGE-m3-ko 1024d · 실프로필 신호).
    # 질문↔프로필신호 코사인은 0.13~0.42 대역이라 SemanticClassifier의 0.6을 그대로
    # 빌려오면 **가드가 통째로 무력**해진다(격자 탐색: 0.6에선 4건 중 0건 적중).
    #   - 절대 유사도는 변별력이 없다: 깨야 함 0.154~0.424 vs 지켜야 함 0.132~0.326 (겹침)
    #   - **마진이 신호다**: 깨야 함 +0.060~+0.089 vs 지켜야 함 전부 음수(-0.030~-0.064)
    # 채택값에서 깨야 함 3/4 적중, 지켜야 함 0/10 오파괴. 여유: 깨기쪽 +0.02 / 지키기쪽 +0.07
    # (오파괴가 더 큰 손해라 지키기 쪽에 여유를 더 줬다).
    # 미적중 1건("보험금 청구 어떻게 해")은 임계 문제가 아니다 — 임베딩이 사주(0.219)를
    # 보험(0.154)보다 가깝게 본다. 이 방법으로는 못 잡는 한계이며 V4~V6 토큰 매칭의 몫.
    sticky_break_similarity: float = 0.25  # 노이즈 바닥 — 둘 다 미미한데 마진만 큰 경우 차단
    sticky_break_margin: float = 0.04      # 타 도메인이 sticky보다 이만큼 앞서야 깬다
    # DGX Spark(원격 GPU, Tailscale) ollama 서빙 — 설정 시 모든 LLM 역할(main·리포트·
    # 라우터·오케스트레이션·무료콘텐츠)을 DGX가 우선 담당하고, 연결 단절 시 현행 로컬
    # 구조로 자동 폴백(FailoverLLMProvider). 임베딩·리랭커는 대상 아님(LLM 아님).
    dgx_llm_url: str = ""          # 예: http://100.102.16.62:11434
    dgx_main_model: str = "qwen3.6:35b-a3b"
    # 역할별 DGX 모델 오버라이드. ""이면 전부 dgx_main_model 하나를 공유한다(권장).
    # 다른 모델을 섞으면 ollama 동시 상주 한계(기본 3)에 걸려 evict↔reload가 돌고,
    # 매 스왑마다 콜드로드를 문다(실측: gpt-oss:120b 로드 143초 + qwen3.6 evict).
    # dgx_main_model은 MoE(활성 3B)라 분류에도 7B급과 지연이 같아 쪼갤 실익이 없다.
    dgx_report_model: str = ""
    dgx_router_model: str = ""
    dgx_orchestrator_model: str = ""
    dgx_fortune_model: str = ""
    # 로컬 MLX 폴백 사용 여부. True(기본) = DGX 단절 시 현행 로컬 구조로 자동 폴백
    # (FailoverLLMProvider). 로컬 MLX LLM 서버(8104 리포트·8105 라우터·8106 메인/무료)는
    # 폴백으로 상시 기동해 두는 것이 운영 기준 — 맥 메모리를 점유하는 대가로 DGX·Tailscale
    # 단절이 전 LLM 정지로 번지지 않게 한다.
    # False로 두면 DGX 단독 = 단절 시 라우팅·생성·리포트가 동시에 멈춘다.
    dgx_local_fallback: bool = True

    # 최종 답변(main) LLM만 백엔드 강제 오버라이드. ""=provider_mode 따름.
    # "anthropic"이면 판단 레이어(라우터·플래너)는 로컬 유지, 생성만 상용 —
    # 로컬 LLM 원칙("상용은 최종 답변 LLM만 스왑") 그대로의 스위치.
    main_llm_backend: str = ""
    greeting_max_length: int = 30
    pattern_max_query_length: int = 30

    # Planner (Plan-and-Execute 아키텍처)
    planner_enabled: bool = True         # 글로벌 킬스위치
    planner_timeout: float = 5.0         # Planner LLM 호출 타임아웃 (초)
    planner_max_retries: int = 2         # Adaptive Retry Loop 최대 재시도

    # 사주 백엔드 (saju_lookup 도구가 호출하는 사주 데이터 서비스)
    saju_backend_url: str = "http://localhost:8002"

    # FlowSNS 연동 (flowsns_* 도구가 호출하는 FlowSNS API)
    flowsns_api_url: str = "http://localhost:3001"
    flowsns_api_key: str = ""
    flowsns_timeout: float = 15.0

    # 서버
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
