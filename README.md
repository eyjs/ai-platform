# AI Platform

> **최근 업데이트 (2026-04-20): 중앙화 고도화 완료.**
> - Provider Capability Metadata + Anthropic Stub + Registry (`apps/api/src/infrastructure/providers/`)
> - API Key 관리 (BFF CRUD + audit + rotate), Admin UI (`apps/frontend/app/admin/api-keys/`)
> - Profile YAML JSON Schema 검증 + history diff/rollback (`apps/bff/src/profiles/schema/`)
> - 키별 사용량 대시보드 (`/admin/api-keys/[id]`) — 요청 수, p50/p95, 캐시 적중률
> - 응답 캐시 (PostgreSQL `response_cache` + TTL sweeper)
> - Provider 라우팅 정책 (YAML `providers:` 블록 + 2단 fallback)
> - Alembic 010~013 마이그레이션
>
> 상세 문서:
> - [Provider Switching](docs/provider-switching.md)
> - [API Key Management](docs/api-key-management.md)
> - [Profile YAML Schema](docs/profile-yaml-schema.md)
> - [Provider Routing Policy](docs/provider-routing-policy.md)

**Profile 기반 범용 AI 에이전트 플랫폼.**

API Key 하나와 Profile YAML만으로 도메인별 AI 챗봇을 생성한다.
Agent는 하나(Universal Agent), 행동은 Profile이 결정한다. 코드 변경 0줄.

```
외부 웹사이트  ──  <script src="ai-platform/widget.js">  ──  챗봇 동작
                        |
                   API Key + Profile 헤더
                        |
                   AI Platform (이 프로젝트)
                        |
                   문서 수집 API  <──  KMS, DMS, CMS 등 외부 시스템
```

## How It Works

```
1. Profile 생성    "보험 상담 챗봇 만들어줘"     ->  insurance-qa.yaml
2. 문서 수집       보험 약관, 상품요약서 등       ->  POST /api/documents/ingest
3. 챗봇 동작       "대인배상 보상한도 알려줘"     ->  문서 기반 RAG 답변
```

Profile을 만들고 문서를 밀어넣으면 끝. 코드 변경 없이 새 도메인 챗봇이 바로 동작한다.

---

## Architecture

### 시스템 흐름

```
Request
  |
  v
[Orchestrator] ── 프로필 선택 (어떤 도메인 전문가에게 보낼지)
  |                 Tier 1: 임베딩 유사도 (~100ms, 다국어)
  |                 Tier 1-B: 패턴 매칭 (폴백)
  |                 Tier 2: 키워드 스코어링
  |                 Tier 3: LLM Function Calling (최후 수단)
  v
[Router 4-Layer] ── 실행 전략 결정 (어떻게 답변할지)
  |  L0: Context Resolver (대명사 해소, LLM 위임)
  |  L1: Intent Classifier (QuestionType 분류)
  |  L2: Mode Selector (deterministic/agentic/workflow)
  |  L3: Strategy Builder (ExecutionPlan 조립)
  v
[Agent] ── LangGraph 파이프라인 실행
  |  route_by_rag -> execute_tools -> graph_enrich -> generate -> guardrails -> response
  v
[Tool System] ── RAG 5-Layer Pipeline
  |  L1: Adaptive Query Expansion (probe -> 조건부 LLM 확장)
  |  L2: Noise Filter (score gap 기반)
  |  L3: Neighbor Expansion (인접 chunk)
  |  L4: 3-Tier Reranking (CrossEncoder + score fusion)
  |  L5: Result Guard (PII masking)
  v
[Safety Guard Chain] ── 답변 검증
  |  FaithfulnessGuard (숫자/인용/co-occurrence/LLM deep eval)
  |  PIIFilterGuard (개인정보 마스킹)
  |  ResponsePolicyGuard (strict/balanced)
  v
[SSE Streaming] ── 토큰 단위 + trace 이벤트 실시간 전송
```

### 레이어 의존 규칙

```
Gateway -> Orchestrator -> Router -> Agent -> Tool -> Domain/Infrastructure
                                                   -> Safety
```

**단방향만 허용. 역방향 참조 금지.**

### 3-Layer 설정 체계

```
Platform Settings (config.py)      -- 인프라, 시크릿, 임계값 (환경변수 AIP_ prefix)
Locale Bundle (locale/ko.yaml)     -- 언어별 문자열, 패턴, PII 규칙 (YAML)
Profile YAML (seeds/profiles/)     -- 도메인 행동, system_prompt, 도구, 가드레일
```

하드코딩 제로. 프롬프트/메시지/패턴/PII 규칙 전부 locale YAML로 외부화.

### 13 Components

| # | Component | 파일 수 | 역할 |
|---|-----------|---------|------|
| C1 | **Gateway** | 8 | 인증(JWT/API Key), SSE 스트리밍, Rate Limiting, CORS |
| C2 | **Orchestrator** | 8 | 임베딩 기반 프로필 라우팅 + 3-Tier 폴백 + 크로스도메인 핸드오프 |
| C3 | **Router** | 7 | 4-Layer 질문 분석 (Context -> Intent -> Mode -> Strategy) |
| C4 | **Agent** | 10 | LangGraph 실행 (deterministic/agentic), thinking 분리 |
| C5 | **Workflow** | 5 | 절차 기반 대화 (예약, 계약 등), 뒤로가기/이탈 처리 |
| C6 | **Tool System** | 11 | Registry + Permission + Scope 주입 + RAG 5-Layer |
| C7 | **Domain** | 5 | AgentProfile, SearchScope, 공유 모델 (SSOT) |
| C8 | **Infrastructure** | 26 | PostgreSQL 단일 스택, Provider Factory, Memory |
| C9 | **Safety** | 5 | FaithfulnessGuard, PIIFilter, ResponsePolicy (동적 체인) |
| C10 | **Pipeline** | 4 | Parse -> Chunk -> Embed -> Index (비동기 Job Queue) |
| C11 | **Observability** | 4 | 구조화 JSON 로깅, RequestTrace, 레이턴시 추적 |
| C12 | **Locale** | 2 | 다국어 문자열/패턴/PII 규칙 (ko.yaml -> LocaleBundle) |
| C13 | **Services** | 2 | KMS 지식그래프 클라이언트 |

### Orchestrator: 임베딩 기반 프로필 라우팅

```
질문 -> 임베딩 -> 프로필 능력 기술과 cosine similarity -> 최적 프로필 선택
```

- 프로필의 system_prompt, description, domain_scopes를 임베딩하여 "능력 기술"으로 사용
- 대표 질문 관리 불필요 -- 프로필 YAML만 잘 쓰면 라우팅 자동
- 다국어 지원 (BGE-m3-ko 임베딩 모델)
- threshold 미달 시 패턴/키워드/LLM 순차 폴백

### RAG 5-Layer Pipeline

```
Query -> [L1 Probe+확장] -> [검색] -> [L2 노이즈필터] -> [L3 이웃확장] -> [L4 리랭킹] -> [L5 PII]
```

| Layer | 역할 | 알고리즘 |
|-------|------|----------|
| L1 | Adaptive Query Expansion | probe 검색 후 score < threshold면 LLM으로 쿼리 변형 |
| Hybrid Search | 벡터+FTS+Trigram 3중 검색 | pgvector ANN + tsvector FTS + pg_trgm fuzzy + RRF 병합 |
| L2 | Noise Filter | score gap ratio 기반 저품질 제거 |
| L3 | Neighbor Expansion | 상위 chunk의 인접 chunk 가져오기 |
| L4 | 3-Tier Reranking | CrossEncoder + vector score fusion, Tier1+Tier2 보충 |
| L5 | Result Guard | PII 마스킹 (locale 기반 패턴) |

### Qwen3 Thinking Mode 지원

```
LLM 응답: <think>추론 과정...</think>실제 답변

-> HttpLLMProvider가 <think> 블록 분리
-> SSE event:trace (thinking) + event:token (answer)
-> 프론트에서 "생각보기" 토글 가능
-> /no_think 지시로 비활성화 가능 (locale YAML에서 설정)
```

---

## Tech Stack

| 영역 | 기술 |
|------|------|
| 프레임워크 | FastAPI |
| 언어 | Python 3.11+ |
| DB | PostgreSQL 16 + pgvector + pg_trgm |
| 에이전트 | LangGraph (StateGraph + ReAct) |
| 임베딩 | BGE-m3-ko (dev, MPS GPU) / OpenAI (prod) |
| LLM | MLX Qwen3-14B/8B (dev) / OpenAI (prod) |
| 리랭커 | BGE-reranker-v2-m3 (dev, MPS GPU) / HTTP API (prod) |
| 스트리밍 | SSE (sse-starlette) |
| 테스트 | pytest (368 tests) |

### MLX 서버 구성 (Apple Silicon)

| 포트 | 모델 | 역할 |
|------|------|------|
| 8102 | bge-reranker-v2-m3 | 리랭커 (MPS) |
| 8103 | BGE-m3-ko (1024d) | 임베딩 (MPS) |
| 8104 | Qwen3-14B-4bit | 오케스트레이터/라우팅 LLM |
| 8105 | Qwen3-8B-4bit | 최종 답변 LLM |
| 8106 | Qwen2.5-7B-4bit | 다른 시스템용 |

---

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 16 with pgvector extension
- Docker & Docker Compose

### Run with Docker

```bash
git clone https://github.com/eyjs/ai-platform.git
cd ai-platform

cp .env.example .env
# Edit .env with your settings

docker compose up -d

curl http://localhost:8010/api/health
```

### Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,local]"

docker compose up -d postgres
alembic upgrade head

uvicorn src.main:app --reload --port 8000

pytest tests/ -x -v
```

---

## API

### Data Plane

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/profiles` | 프로필 목록 |
| POST | `/api/chat` | 챗봇 (비스트리밍) |
| POST | `/api/chat/stream` | 챗봇 (SSE 스트리밍) |
| POST | `/api/documents/ingest` | 문서 수집 (비동기) |
| GET | `/api/documents/ingest/{job_id}` | 수집 작업 상태 조회 |

### Admin (Control Plane)

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/admin/profiles` | 프로필 CRUD |
| GET/PUT/DELETE | `/api/admin/profiles/{id}` | 프로필 상세/수정/삭제 |
| GET/POST | `/api/admin/workflows` | 워크플로우 CRUD |
| POST | `/api/admin/cache/invalidate` | 캐시 무효화 |
| POST | `/api/api-keys` | API Key 생성 (ADMIN) |

### SSE 이벤트 프로토콜

```
event: trace     -- 파이프라인 추적 (tool 실행, thinking 등)
data: {"step": "tool_execution", "tool": "rag_search", "ms": 764}

event: token     -- 답변 토큰
data: {"delta": "대인배상I의 보상한도는"}

event: done      -- 완료 + 소스
data: {"answer": "...", "sources": [...], "tools_called": [...]}
```

### Chat Example

```bash
# Orchestrator 모드 (프로필 자동 선택)
curl -X POST http://localhost:8010/api/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: aip_dev_admin" \
  -d '{"question": "자동차보험 보험료 알려줘"}'

# 프로필 지정 모드
curl -X POST http://localhost:8010/api/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: aip_dev_admin" \
  -d '{"question": "보험료 알려줘", "chatbot_id": "insurance-qa"}'
```

---

## Project Structure

```
ai-platform/
|-- src/
|   |-- main.py                    # FastAPI entrypoint + lifespan
|   |-- config.py                  # Settings (AIP_ prefix, pydantic-settings)
|   |-- bootstrap.py               # 13개 컴포넌트 초기화 오케스트레이션
|   |
|   |-- locale/                    # C12: 다국어 로케일 시스템
|   |   |-- ko.yaml                #   한국어 (프롬프트, 메시지, 패턴, PII)
|   |   |-- bundle.py              #   LocaleBundle 로더 (startup 시 pre-compile)
|   |
|   |-- gateway/                   # C1: HTTP 레이어
|   |   |-- router.py              #   Data plane (/chat, /ingest, /health)
|   |   |-- admin_router.py        #   Control plane (profile/workflow CRUD)
|   |   |-- auth.py                #   JWT + API Key + Origin 인증
|   |   |-- rate_limiter.py        #   PostgreSQL Token Bucket
|   |   |-- streaming.py           #   SSE 유틸리티
|   |   |-- models.py              #   Request/Response DTO
|   |   |-- webhook_router.py      #   Webhook 핸들러
|   |
|   |-- orchestrator/              # C2: 프로필 라우팅
|   |   |-- orchestrator.py        #   MasterOrchestrator (3-Tier + continuation)
|   |   |-- embedding_router.py    #   임베딩 기반 의도-능력 매칭 (Tier 1)
|   |   |-- profile_router.py      #   패턴/키워드 라우터 (Tier 1-B, 2)
|   |   |-- llm_adapter.py         #   LLM Function Calling (Tier 3)
|   |   |-- prompts.py             #   오케스트레이터 프롬프트 (locale 기반)
|   |   |-- models.py              #   OrchestratorResult
|   |   |-- tenant.py              #   멀티테넌트 필터링
|   |
|   |-- router/                    # C3: 4-Layer 질문 라우팅
|   |   |-- ai_router.py           #   4-Layer 오케스트레이터
|   |   |-- context_resolver.py    #   L0: 대명사 해소 (Pattern detect -> LLM)
|   |   |-- intent_classifier.py   #   L1: QuestionType 분류 (locale 패턴)
|   |   |-- mode_selector.py       #   L2: 모드 선택 (Hybrid 트리거 전수 비교)
|   |   |-- strategy_builder.py    #   L3: ExecutionPlan 조립 + PII 새니타이징
|   |   |-- execution_plan.py      #   ExecutionPlan, QuestionType, QuestionStrategy
|   |
|   |-- agent/                     # C4: Universal Agent
|   |   |-- graph_executor.py      #   모드별 LangGraph 실행 + thinking 분리
|   |   |-- graphs.py              #   StateGraph / ReAct 빌더
|   |   |-- nodes.py               #   노드 팩토리 (tool, generate, guardrail)
|   |   |-- state.py               #   AgentState TypedDict
|   |   |-- tool_adapter.py        #   Tool -> LangChain StructuredTool 변환
|   |   |-- graph_enrich.py        #   KMS 지식그래프 보강 노드
|   |   |-- profile.py             #   AgentProfile re-export
|   |   |-- profile_store.py       #   Profile YAML/DB 로딩
|   |   |-- chat_model_factory.py  #   ChatModel 팩토리 (MLX 자동 감지)
|   |
|   |-- workflow/                  # C5: 절차 기반 대화 엔진
|   |   |-- engine.py              #   WorkflowEngine (start/advance/cancel)
|   |   |-- store.py               #   WorkflowStore (YAML seed + DB)
|   |   |-- definition.py          #   WorkflowDefinition, WorkflowStep
|   |   |-- state.py               #   WorkflowSession
|   |
|   |-- tools/                     # C6: Tool System
|   |   |-- base.py                #   Tool/ScopedTool Protocol, ToolResult
|   |   |-- registry.py            #   ToolRegistry (name -> instance, scope 주입)
|   |   |-- internal/
|   |       |-- rag_search.py      #   5-Layer RAG Pipeline
|   |       |-- fact_lookup.py     #   Fact 검색
|   |       |-- query_expander.py  #   L1: LLM 쿼리 확장 (locale 프롬프트)
|   |       |-- noise_filter.py    #   L2: Score gap 필터
|   |       |-- neighbor_expander.py # L3: 인접 chunk 확장
|   |       |-- reranker_pipeline.py # L4: 3-Tier CrossEncoder 리랭킹
|   |       |-- result_guard.py    #   L5: PII 마스킹 (locale 패턴)
|   |
|   |-- domain/                    # C7: 공유 도메인 모델
|   |   |-- models.py              #   AgentMode, SecurityLevel, SearchScope 등
|   |   |-- agent_profile.py       #   AgentProfile (frozen dataclass)
|   |   |-- agent_context.py       #   AgentContext (tool 실행 컨텍스트)
|   |   |-- protocols.py           #   공유 Protocol
|   |
|   |-- infrastructure/            # C8: PostgreSQL 단일 스택
|   |   |-- vector_store.py        #   Hybrid Search (pgvector + FTS + trigram + RRF)
|   |   |-- fact_store.py          #   Fact 저장/조회
|   |   |-- job_queue.py           #   PostgreSQL SKIP LOCKED 작업 큐
|   |   |-- memory/
|   |   |   |-- session.py         #   세션 메모리 (PostgreSQL)
|   |   |   |-- cache.py           #   TTL 캐시 (PostgreSQL UNLOGGED)
|   |   |-- providers/
|   |       |-- base.py            #   LLMProvider, EmbeddingProvider, StreamChunk
|   |       |-- factory.py         #   ProviderFactory (locale 연동)
|   |       |-- llm/               #   OpenAI, Ollama, HTTP(MLX) + thinking 분리
|   |       |-- embedding/         #   OpenAI, SentenceTransformers, HTTP
|   |       |-- reranking/         #   CrossEncoder, LLM, HTTP
|   |       |-- parsing/           #   Text, LlamaParse
|   |
|   |-- safety/                    # C9: Guardrail Chain
|   |   |-- base.py                #   Guardrail Protocol, GuardrailResult
|   |   |-- faithfulness.py        #   숫자/인용/co-occurrence/LLM 검증
|   |   |-- pii_filter.py          #   PII 감지/마스킹 (locale 패턴)
|   |   |-- response_policy.py     #   strict/balanced 정책
|   |
|   |-- pipeline/                  # C10: 문서 수집 파이프라인
|   |   |-- ingest.py              #   IngestPipeline (parse->chunk->embed->index)
|   |   |-- chunker.py             #   TextChunker, MarkdownChunker
|   |   |-- domain_tagger.py       #   도메인 메타데이터 태깅
|   |
|   |-- observability/             # C11: 관측성
|   |   |-- logging.py             #   구조화 JSON 로깅 + ContextVar
|   |   |-- trace_logger.py        #   RequestTrace (노드별 레이턴시)
|   |   |-- metrics.py             #   메트릭 수집
|   |
|   |-- services/                  # C13: 외부 서비스 클라이언트
|   |   |-- kms_graph_client.py    #   KMS 지식그래프 API
|   |   |-- null_kms_client.py     #   NullObject (KMS 미연결 시)
|   |
|   |-- common/                    # 공통 유틸리티
|       |-- exceptions.py          #   예외 계층 (AIError, GatewayError 등)
|
|-- seeds/
|   |-- profiles/                  # Profile YAML (8개)
|   |   |-- insurance-qa.yaml
|   |   |-- insurance-contract.yaml
|   |   |-- legal-contract.yaml
|   |   |-- hr-onboarding.yaml
|   |   |-- food-recipe.yaml
|   |   |-- fortune-saju.yaml
|   |   |-- general-assistant.yaml
|   |   |-- general-chat.yaml
|   |-- workflows/                 # Workflow YAML (2개)
|
|-- static/
|   |-- admin.html                 # Admin UI
|   |-- chat-widget.html           # Chat Widget (SSE)
|
|-- tests/                         # 368 tests
|-- alembic/                       # DB migrations
|-- docker-compose.yml
|-- pyproject.toml
```

---

## DB Schema (PostgreSQL Only)

| 테이블 | 용도 | 타입 |
|--------|------|------|
| agent_profiles | 프로필 설정 | 일반 |
| documents | 문서 메타데이터 | 일반 |
| document_chunks | 벡터+FTS+trgm 검색 | 일반 |
| facts | 구조화된 팩트 | 일반 |
| conversation_sessions | 대화 세션 | 일반 (영속) |
| cache_entries | 캐시 | UNLOGGED |
| job_queue | 작업 큐 (SKIP LOCKED) | 일반 |
| workflow_states | 워크플로우 상태 | 일반 (영속) |
| api_keys | API Key 관리 | 일반 |

---

## Creating a Profile

### YAML (seeds/profiles/)

```yaml
id: insurance-qa
name: 보험 상담 챗봇

domain_scopes:
  - "자동차보험"
  - "실손보험"
security_level_max: "INTERNAL"

mode: "deterministic"

tools:
  - name: "rag_search"
    config:
      max_vector_chunks: 3
  - name: "fact_lookup"

system_prompt: |
  당신은 보험 상품 전문 상담 AI입니다.
  고객의 질문에 정확하고 친절하게 답변하세요.
  반드시 문서 근거를 인용하여 답변하세요.

response_policy: "strict"
guardrails:
  - "faithfulness"
  - "pii_filter"

intent_hints:
  - name: "INSURANCE_INQUIRY"
    patterns: ["보험", "보장", "보험료", "보험금", "보상"]
    description: "보험 상품, 보장 내용, 보험료 관련 질문"
```

서버 재시작하면 자동 로드. 임베딩 라우터가 system_prompt/description을 임베딩하여 자동 라우팅.

---

## Design Principles

1. **Profile = Chatbot** -- YAML 추가만으로 새 챗봇. 코드 변경 0줄.
2. **No Agent Sprawl** -- Agent는 하나. 행동은 Profile이 결정.
3. **Strict Layer Separation** -- Gateway > Router > Agent > Tool. 역방향 금지.
4. **PostgreSQL Single Stack** -- 벡터, 캐시, 세션, 큐 모두 PostgreSQL. Redis 불필요.
5. **Tool Permission as Security** -- Profile.tools에 없는 도구는 LLM이 존재를 모른다.
6. **Scope Injection** -- Tool은 어떤 봇에서 호출됐는지 모른다. scope/context만 받는다.
7. **Locale as Config** -- 모든 언어/도메인 의존 문자열은 YAML 외부화. 코드에 하드코딩 없음.
8. **Capability-based Routing** -- 프로필의 능력 기술을 임베딩하여 의미 기반 라우팅. 패턴 관리 불필요.

---

## KMS Integration

```
KMS (NestJS)                    ai-platform (FastAPI)
--------------                  ---------------------
문서 체계 관리 (SSOT)            AI 프로필 / RAG / 챗봇
도메인/카테고리/문서              검색 / 임베딩 / 생성
        |                               |
        +---- Docker 내부 통신 ---------+
             X-Internal-Key (공유 비밀키)
```

| 설정 | 환경변수 | 설명 |
|------|----------|------|
| KMS API URL | `AIP_KMS_API_URL` | 예: `http://kms-api:3000/api` |
| Internal Key | `AIP_KMS_INTERNAL_KEY` | KMS `INTERNAL_KEY`와 동일한 값 |

---

## Environment Variables

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AIP_PROVIDER_MODE` | development | development / openai / production |
| `AIP_DATABASE_URL` | localhost:5434 | PostgreSQL 연결 |
| `AIP_EMBEDDING_SERVER_URL` | - | MLX 임베딩 서버 |
| `AIP_RERANKER_SERVER_URL` | - | MLX 리랭커 서버 |
| `AIP_ROUTER_LLM_SERVER_URL` | - | 라우팅 LLM 서버 |
| `AIP_MAIN_LLM_SERVER_URL` | - | 답변 LLM 서버 |
| `AIP_AUTH_REQUIRED` | true | 인증 활성화 |
| `AIP_LOCALE` | ko | 로케일 (ko.yaml 로드) |
| `AIP_ORCHESTRATOR_ENABLED` | true | 오케스트레이터 활성화 |
| `AIP_ORCHESTRATOR_MODEL` | Qwen3-14B | 오케스트레이터 LLM 모델 |
| `AIP_LOG_FORMAT` | json | json / human |

---

## License

MIT
