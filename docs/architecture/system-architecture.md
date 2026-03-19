# AI Platform 시스템 아키텍처

## 개요

AI Platform은 멀티프로필 기반 엔터프라이즈 챗봇 플랫폼이다.
프로필(에이전트)별로 독립적인 도메인 지식과 워크플로우를 가지며,
Master Orchestrator가 사용자 질문을 분석하여 적절한 프로필로 자동 라우팅한다.

**핵심 설계 원칙:**
- `chatbot_id` 직접 지정 시 기존 흐름 그대로 동작 (하위 호환)
- `chatbot_id` 미지정 시 MasterOrchestrator가 자동 라우팅
- PostgreSQL only (Redis 없음)
- Provider 추상화로 개발/운영 모드 전환

---

## 전체 시스템 구조도

```mermaid
graph TB
    Client["Client<br/>(Widget / Web / API)"]

    subgraph Gateway["Gateway Layer"]
        Auth["AuthService<br/>JWT + API Key"]
        RL["RateLimiter<br/>Token Bucket"]
        Router["FastAPI Router<br/>/chat, /chat/stream"]
        Admin["Admin Router<br/>/admin/*"]
    end

    subgraph Orchestrator["Orchestrator Layer"]
        MO["MasterOrchestrator<br/>프로필 자동 라우팅"]
        OrcLLM["OrchestratorLLM<br/>GPT-4o / Claude Opus"]
        Tenant["TenantService<br/>멀티테넌트 격리"]
    end

    subgraph Router_Layer["AI Router Layer"]
        AIRouter["AIRouter<br/>L0~L3 분석"]
        IC["IntentClassifier"]
        CR["ContextResolver"]
        MS["ModeSelector"]
        SB["StrategyBuilder"]
        EP["ExecutionPlan"]
    end

    subgraph Agent_Layer["Agent Layer"]
        GE["GraphExecutor<br/>LangGraph"]
        CM["ChatModel<br/>Ollama / OpenAI"]
        TR["ToolRegistry"]
        RAG["RAGSearchTool"]
        Fact["FactLookupTool"]
    end

    subgraph Safety["Safety Layer"]
        FG["FaithfulnessGuard"]
        PII["PIIFilterGuard"]
        RP["ResponsePolicyGuard"]
    end

    subgraph Workflow_Layer["Workflow Layer"]
        WE["WorkflowEngine"]
        WS["WorkflowStore"]
    end

    subgraph Infra["Infrastructure Layer"]
        VS["VectorStore<br/>pgvector"]
        FS["FactStore"]
        SM["SessionMemory"]
        Cache["PgCache"]
        JQ["JobQueue"]
        PF["ProviderFactory"]
    end

    subgraph Providers["Provider Abstraction"]
        Embed["EmbeddingProvider<br/>BGE-m3-ko / OpenAI"]
        LLM["LLM Provider<br/>Ollama / OpenAI"]
        Parse["ParsingProvider<br/>LlamaParse / Text"]
        Rerank["RerankerProvider<br/>CrossEncoder / HTTP"]
    end

    DB[("PostgreSQL<br/>+ pgvector")]

    Client --> Auth
    Auth --> RL
    RL --> Router
    Router --> MO
    MO --> OrcLLM
    MO --> Tenant
    MO --> SM
    Router --> AIRouter
    AIRouter --> IC
    AIRouter --> CR
    AIRouter --> MS
    AIRouter --> SB
    SB --> EP
    EP --> GE
    GE --> CM
    GE --> TR
    TR --> RAG
    TR --> Fact
    GE --> Safety
    Router --> WE
    WE --> WS
    RAG --> VS
    RAG --> Embed
    RAG --> Rerank
    Fact --> FS
    VS --> DB
    FS --> DB
    SM --> DB
    Cache --> DB
    JQ --> DB
    Tenant --> DB
    PF --> Providers
```

---

## 요청 처리 흐름

```mermaid
sequenceDiagram
    participant C as Client
    participant A as AuthService
    participant R as Router
    participant O as Orchestrator
    participant AI as AIRouter
    participant G as GraphExecutor
    participant T as ToolRegistry
    participant V as VectorStore

    C->>A: POST /chat/stream<br/>{question, session_id, chatbot_id?}
    A->>A: JWT/API Key 검증
    A->>R: UserContext

    alt chatbot_id 미지정
        R->>O: route(question, session_id, user_ctx)
        O->>O: 연속대화 휴리스틱 확인
        alt 연속 대화
            O-->>R: OrchestratorResult(is_continuation=true)
        else 새로운 질문
            O->>O: LLM Function Calling
            O-->>R: OrchestratorResult(profile_id)
        end
    end

    R->>AI: analyze(question, profile, history)
    AI->>AI: L0 Intent + L1 Context + L2 Mode + L3 Strategy
    AI-->>R: ExecutionPlan

    R->>G: execute_stream(plan, context)
    G->>T: RAGSearchTool.run()
    T->>V: hybrid_search()
    V-->>T: chunks + scores
    T-->>G: search results
    G->>G: LLM 응답 생성 + Guardrail 검사
    G-->>C: SSE Stream (answer + sources + trace)
```

---

## Orchestrator 라우팅 로직

```mermaid
flowchart TD
    Start["chatbot_id 미지정 요청"]
    Profiles["테넌트 기반 프로필 필터링"]
    NoProfiles{"프로필 존재?"}
    NoProfileMsg["일반 응답: 서비스 없음"]
    LoadMeta["세션 메타데이터 로드"]
    ResumeCheck{"워크플로우<br/>재개 의도?"}
    Resume["워크플로우 재개<br/>(paused_state 복원)"]
    ContCheck{"연속 대화<br/>패턴?"}
    Continuation["현재 프로필 유지<br/>(LLM 호출 없음)"]
    LLM["LLM Function Calling<br/>프로필 선택"]
    General{"일반 응답?<br/>(인사/잡담)"}
    GeneralMsg["직접 응답 반환"]
    Selected["선택된 프로필로<br/>라우팅"]
    SwitchCheck{"프로필 전환?"}
    Pause["활성 워크플로우<br/>일시정지"]

    Start --> Profiles
    Profiles --> NoProfiles
    NoProfiles -->|No| NoProfileMsg
    NoProfiles -->|Yes| LoadMeta
    LoadMeta --> ResumeCheck
    ResumeCheck -->|Yes| Resume
    ResumeCheck -->|No| ContCheck
    ContCheck -->|Yes| Continuation
    ContCheck -->|No| LLM
    LLM --> General
    General -->|Yes| GeneralMsg
    General -->|No| Selected
    Selected --> SwitchCheck
    SwitchCheck -->|Yes| Pause
    SwitchCheck -->|No| Selected

    style NoProfileMsg fill:#f99,stroke:#c00
    style Resume fill:#9f9,stroke:#0c0
    style Continuation fill:#9cf,stroke:#06c
    style GeneralMsg fill:#fc9,stroke:#c60
    style Pause fill:#f9f,stroke:#c0c
```

---

## 멀티테넌트 격리 구조

```mermaid
erDiagram
    tenants {
        varchar id PK
        varchar name
        text description
        boolean orchestrator_enabled
        varchar default_chatbot_id
        boolean is_active
        timestamptz created_at
        timestamptz updated_at
    }

    tenant_profiles {
        varchar tenant_id FK
        varchar profile_id FK
    }

    api_keys {
        varchar key_hash PK
        varchar name
        varchar user_id
        varchar user_role
        varchar security_level_max
        varchar tenant_id FK
        text[] allowed_profiles
        text[] allowed_origins
        int rate_limit_per_min
        timestamptz expires_at
        boolean is_active
    }

    agent_profiles {
        varchar id PK
        varchar name
        text description
        text system_prompt
    }

    tenants ||--o{ tenant_profiles : "has"
    agent_profiles ||--o{ tenant_profiles : "assigned to"
    tenants ||--o{ api_keys : "owns"
```

```mermaid
flowchart LR
    subgraph TenantA["Tenant A (설계사)"]
        A_KEY["API Key A<br/>tenant_id: tenant-a"]
        A_PROFILES["30개 프로필 전체"]
    end

    subgraph TenantB["Tenant B (고객)"]
        B_KEY["API Key B<br/>tenant_id: tenant-b"]
        B_PROFILES["4개 프로필만"]
    end

    A_KEY --> A_PROFILES
    B_KEY --> B_PROFILES

    style TenantA fill:#e8f5e9,stroke:#4caf50
    style TenantB fill:#fff3e0,stroke:#ff9800
```

---

## 워크플로우 일시정지/재개

```mermaid
sequenceDiagram
    participant U as 사용자
    participant O as Orchestrator
    participant WE as WorkflowEngine
    participant SM as SessionMemory

    Note over U,SM: 워크플로우 진행 중 (step 3/5)

    U->>O: "수수료 얼마?" (off-topic)
    O->>WE: get_session(session_id)
    WE-->>O: active workflow (step_3, collected: {name, type})

    O->>O: LLM 판단: 현재 워크플로우와 무관
    O->>SM: save paused_workflow<br/>{workflow_id, step_3, collected}
    O->>WE: cancel(session_id)
    O-->>U: fee-calc 프로필로 라우팅

    Note over U,SM: 수수료 프로필 응답 후...

    U->>O: "다시 계약 이어서"
    O->>O: resume_intent 감지
    O->>SM: get paused_workflow
    SM-->>O: {workflow_id, step_3, collected}
    O->>WE: resume(workflow_id, session_id, step_3, collected)
    O-->>U: 보험계약 프로필, step 3부터 재개
```

---

## 디렉토리 구조

```
src/
|-- main.py                          # FastAPI 앱 엔트리포인트
|-- bootstrap.py                     # 앱 상태 초기화 (15개 컴포넌트)
|-- config.py                        # Settings (환경변수)
|-- worker_main.py                   # Worker 프로세스 엔트리포인트
|
|-- gateway/                         # API 게이트웨이
|   |-- router.py                    # /chat, /chat/stream, /health
|   |-- admin_router.py              # /admin/* (프로필, 테넌트 관리)
|   |-- webhook_router.py            # /webhooks
|   |-- auth.py                      # JWT + API Key 인증
|   |-- models.py                    # 요청/응답 모델
|   |-- rate_limiter.py              # PostgreSQL Token Bucket
|   +-- streaming.py                 # SSE 스트리밍 유틸
|
|-- orchestrator/                    # Master Orchestrator (신규)
|   |-- orchestrator.py              # MasterOrchestrator.route()
|   |-- llm_adapter.py              # OpenAI/Anthropic Function Calling
|   |-- prompts.py                   # LLM 프롬프트 + Tool 정의
|   |-- models.py                    # OrchestratorResult, TenantConfig
|   +-- tenant.py                    # TenantService (멀티테넌트)
|
|-- router/                          # AI 라우터 (질문 분석)
|   |-- ai_router.py                 # L0~L3 분석 파이프라인
|   |-- intent_classifier.py         # 의도 분류
|   |-- context_resolver.py          # 맥락 해석
|   |-- mode_selector.py             # 에이전트 모드 결정
|   |-- strategy_builder.py          # 실행 전략 조합
|   +-- execution_plan.py            # ExecutionPlan 모델
|
|-- agent/                           # 에이전트 실행
|   |-- graph_executor.py            # LangGraph 실행기
|   |-- graphs.py                    # 그래프 정의
|   |-- nodes.py                     # 그래프 노드 (분석/검색/생성)
|   |-- state.py                     # 그래프 상태
|   |-- profile.py                   # 프로필 모델
|   |-- profile_store.py             # 프로필 저장소
|   |-- chat_model_factory.py        # ChatModel 팩토리
|   +-- tool_adapter.py              # 도구 어댑터
|
|-- tools/                           # 에이전트 도구
|   |-- base.py                      # 도구 기본 클래스
|   |-- registry.py                  # ToolRegistry
|   +-- internal/
|       |-- rag_search.py            # RAG 검색 도구
|       +-- fact_lookup.py           # 팩트 조회 도구
|
|-- safety/                          # 안전 가드레일
|   |-- base.py                      # 가드 기본 클래스
|   |-- faithfulness.py              # 충실성 검증
|   |-- pii_filter.py                # 개인정보 필터
|   +-- response_policy.py           # 응답 정책
|
|-- workflow/                        # 워크플로우 엔진
|   |-- engine.py                    # WorkflowEngine (start/advance/resume)
|   |-- store.py                     # WorkflowStore (YAML 시드)
|   |-- definition.py                # 워크플로우 정의 모델
|   +-- state.py                     # 워크플로우 상태
|
|-- infrastructure/                  # 인프라스트럭처
|   |-- vector_store.py              # PostgreSQL + pgvector
|   |-- fact_store.py                # 팩트 저장소
|   |-- job_queue.py                 # 작업 큐
|   |-- memory/
|   |   |-- session.py               # 세션 메모리
|   |   +-- cache.py                 # PgCache
|   +-- providers/                   # Provider 추상화
|       |-- factory.py               # ProviderFactory
|       |-- base.py                  # 기본 인터페이스
|       |-- embedding/               # 임베딩 (BGE-m3-ko, OpenAI, HTTP)
|       |-- llm/                     # LLM (Ollama, OpenAI, HTTP)
|       |-- parsing/                 # 파싱 (LlamaParse, Text)
|       +-- reranking/               # 리랭킹 (CrossEncoder, HTTP, LLM)
|
|-- observability/                   # 관측성
|   |-- logging.py                   # 구조화 로깅
|   |-- metrics.py                   # 메트릭
|   +-- trace_logger.py              # 요청 추적
|
+-- domain/                          # 도메인 모델
    +-- models.py                    # AgentMode, AgentResponse 등
```

---

## Provider 추상화

```mermaid
graph LR
    PF["ProviderFactory"]

    subgraph Dev["개발 모드"]
        E1["SentenceTransformers<br/>BGE-m3-ko"]
        L1["Ollama<br/>qwen3:8b"]
        R1["CrossEncoder<br/>BGE-reranker-v2-m3"]
        P1["TextParser"]
    end

    subgraph Prod["운영 모드"]
        E2["OpenAI<br/>text-embedding-3-small"]
        L2["OpenAI<br/>gpt-4o-mini"]
        R2["HTTP Reranker"]
        P2["LlamaParse"]
    end

    PF -->|provider_mode=local| Dev
    PF -->|provider_mode=openai| Prod

    style Dev fill:#e3f2fd,stroke:#1565c0
    style Prod fill:#fce4ec,stroke:#c62828
```

---

## 인증 흐름

```mermaid
flowchart TD
    Req["요청 수신"]
    AuthReq{"auth_required?"}
    Anon["Anonymous VIEWER 반환"]
    HasJWT{"Authorization<br/>Bearer 헤더?"}
    HasKey{"X-API-Key 헤더?"}
    NoAuth["AuthError: 인증 필요"]

    JWT["JWT 검증"]
    JWTValid{"유효?"}
    JWTCtx["UserContext 생성<br/>(JWT claims)"]
    JWTErr["AuthError"]

    APIKey["API Key 해시 → DB 조회"]
    KeyValid{"존재 + 미만료?"}
    KeyCtx["UserContext 생성<br/>(DB 결과 + tenant_id)"]
    KeyErr["AuthError"]

    Req --> AuthReq
    AuthReq -->|No| Anon
    AuthReq -->|Yes| HasJWT
    HasJWT -->|Yes| JWT
    HasJWT -->|No| HasKey
    HasKey -->|Yes| APIKey
    HasKey -->|No| NoAuth

    JWT --> JWTValid
    JWTValid -->|Yes| JWTCtx
    JWTValid -->|No| JWTErr

    APIKey --> KeyValid
    KeyValid -->|Yes| KeyCtx
    KeyValid -->|No| KeyErr

    style Anon fill:#e8f5e9
    style JWTCtx fill:#e3f2fd
    style KeyCtx fill:#e3f2fd
    style NoAuth fill:#ffcdd2
    style JWTErr fill:#ffcdd2
    style KeyErr fill:#ffcdd2
```

---

## 앱 초기화 순서 (bootstrap.py)

```mermaid
graph TD
    S["Settings 로드"]
    VS["1. VectorStore<br/>(PostgreSQL + pgvector)"]
    AS["2. AuthService<br/>(JWT + API Key)"]
    FS["3. FactStore + SessionMemory + PgCache"]
    PF["4. ProviderFactory<br/>(Embedding, LLM, Reranker)"]
    PS["5. ProfileStore<br/>(시드 로드)"]
    Tools["6. ToolRegistry<br/>(RAGSearch, FactLookup)"]
    AR["7. AIRouter"]
    Guard["8. Guardrails<br/>(Faithfulness, PII, Policy)"]
    CM["9. ChatModel + GraphExecutor"]
    WF["10. WorkflowEngine + Store"]
    IP["11. IngestPipeline"]
    JQ["12. JobQueue"]
    RL["13. RateLimiter"]
    TS["14. TenantService"]
    MO["15. MasterOrchestrator<br/>(최상위 LLM)"]

    S --> VS
    VS --> AS
    VS --> FS
    VS --> PF
    PF --> PS
    PS --> Tools
    PF --> AR
    Guard --> CM
    Tools --> CM
    CM --> WF
    PF --> IP
    VS --> JQ
    VS --> RL
    VS --> TS
    TS --> MO
    PS --> MO
    FS --> MO
    WF --> MO
```

---

## 기술 스택

| 영역 | 기술 | 용도 |
|------|------|------|
| 프레임워크 | FastAPI | API 서버 |
| DB | PostgreSQL + pgvector | 데이터 + 벡터 검색 |
| DB 드라이버 | asyncpg | 비동기 PostgreSQL |
| 마이그레이션 | Alembic | 스키마 관리 |
| 에이전트 | LangGraph | 그래프 기반 실행 |
| 스트리밍 | SSE (sse-starlette) | 실시간 응답 |
| 인증 | PyJWT + SHA-256 | JWT + API Key |
| 로깅 | structlog | 구조화 로깅 |
| 설정 | pydantic-settings | 환경변수 관리 |
| Orchestrator LLM | OpenAI / Anthropic | Function Calling |
