# AI Platform - Universal Agent Platform

## 프로젝트 개요

Profile 기반 범용 AI 에이전트 플랫폼.
ChatGPT GPTs처럼 설정(YAML)만으로 도메인별 AI 챗봇을 생성/운영한다.
Agent는 하나(Universal Agent), 행동은 Profile이 결정한다.

## 핵심 설계 원칙

### 1. Agent Sprawl 방지
- Agent는 하나(Universal Agent Runtime), 행동은 Profile이 결정
- 새 챗봇 = Profile YAML 추가, 코드 변경 0줄

### 2. 레이어 책임 분리 (절대 깨지면 안 됨)
```
Gateway  → "누구인지" 해석 (인증, Profile 로딩, UserContext)
Router   → "뭘 할지" 결정 (Intent, Mode, Strategy)
Agent    → "어떻게 할지" 실행 (Tool 선택, 답변 생성)
Tool     → "실제 작업" 수행 (RAG 검색, scope/context 자동 주입)
```
각 레이어는 상위/하위의 내부 구현을 알지 않는다.
레이어 간 의존은 단방향: Gateway→Router→Agent→Tool. 역방향 참조 금지.

### 3. 인프라 최소화 — PostgreSQL 단일 스택
| 기존 (Redis) | 대체 (PostgreSQL) |
|---|---|
| 세션 캐시 | UNLOGGED TABLE + JSONB + expires_at |
| Workflow State | 일반 테이블 + JSONB (영속) |
| Pub/Sub 이벤트 | LISTEN/NOTIFY |
| 작업 큐 | SELECT FOR UPDATE SKIP LOCKED |
| TTL 만료 정리 | 주기적 DELETE (asyncio task) |

### 4. Tool Permission이 보안의 핵심
- tools 리스트에 없는 도구는 LLM이 존재를 모른다
- Tool 내부에서 "어떤 봇에서 호출됐는지" 알면 안 된다
- scope/context를 받아서 동작

## 구현 시 절대 규칙

1. **Profile YAML 추가만으로 새 챗봇이 동작해야 한다.** 코드 변경 필요하면 설계가 틀린 것
2. **Tool은 scope/context를 받아서 동작한다.** Tool 내부에서 봇 식별 금지
3. **Router는 Profile을 읽을 뿐 하드코딩하지 않는다.** `if profile == "..."` 코드 금지
4. **PostgreSQL 단일 스택.** Redis, Elasticsearch 등 추가 인프라 도입 금지
5. **레이어 간 의존은 단방향이다.** Gateway→Router→Agent→Tool
6. **ai-worker 자산은 복사 후 확장한다.** import로 참조하지 않는다

## 12 컴포넌트

```
C1.  AI Gateway          — 인증, Profile 로딩, UserContext, SSE
C2.  AI Router           — 4-Layer (Context→Intent→Mode→Strategy)
C2-A AgentProfile        — 챗봇 설정 단위 (= GPTs)
C3.  Universal Agent     — 결정론적 파이프라인, Profile 기반 동작
C4.  Workflow Engine     — 절차 기반 대화 (MVP 이후)
C5.  Tool System         — Registry + Permission + Scope 주입
C6.  Domain Layer        — SearchScope, Context Builder
C7.  Infrastructure      — PostgreSQL 단일 (벡터/캐시/큐/세션)
C8.  Safety Guard        — Faithfulness, PII, ResponsePolicy (동적 체인)
C9.  Observability       — 라우팅 로그, Tool latency
C10. Knowledge Pipeline  — Parse→Chunk→Embed→Index
C11. Experiment Layer    — 향후
C12. Memory System       — Short/Session (PostgreSQL 기반)
```

## Router 4-Layer

```
Layer 0: Context Resolver  — 대명사 해소 (ChainResolver: Pattern→LLM)
Layer 1: Intent Classifier — 패턴매칭 → QuestionType 8종 분류
Layer 2: Mode Selector     — Profile.mode + Intent → agentic/workflow
Layer 3: Strategy Builder  — ExecutionPlan 생성 (scope, tools, prompt, guardrails)
```

## 기술 스택

| 영역 | 기술 |
|---|---|
| 프레임워크 | FastAPI |
| 언어 | Python 3.11+ |
| DB | PostgreSQL 16 + pgvector + pg_trgm |
| 워크플로우 | LangGraph (향후) |
| 임베딩 | sentence-transformers (dev) / OpenAI (prod) |
| LLM | Ollama (dev) / OpenAI (prod) |
| 리랭커 | CrossEncoder (dev) / HTTP (prod) |
| 스트리밍 | SSE (sse-starlette) |

## 디렉토리 구조

```
ai-platform/
├── CLAUDE.md
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml        # PostgreSQL only (Redis 없음)
├── alembic.ini
├── alembic/versions/
├── src/
│   ├── main.py               # FastAPI + lifespan
│   ├── config.py             # Settings (AIP_ prefix)
│   ├── gateway/              # C1: 인증, SSE, 엔드포인트
│   ├── router/               # C2: 4-Layer Router
│   ├── agent/                # C3: Universal Agent, Profile
│   ├── workflow/             # C4: Workflow Engine (MVP 이후)
│   ├── tools/                # C5: Tool Protocol, Registry
│   │   └── internal/         # 내장 도구 (rag_search, fact_lookup)
│   ├── domain/               # C6: Context Builder
│   ├── infrastructure/       # C7: PostgreSQL 기반 전체 인프라
│   │   ├── providers/        # LLM/Embedding/Reranker
│   │   └── memory/           # Session, Cache (PostgreSQL)
│   ├── safety/               # C8: Guardrail Chain
│   ├── observability/        # C9: 메트릭, 로그
│   └── pipeline/             # C10: Knowledge Pipeline
├── seeds/
│   ├── profiles/             # Profile YAML (3개)
│   └── workflows/            # Workflow YAML (1개)
└── tests/
```

## DB 스키마 (PostgreSQL only)

| 테이블 | 용도 | 타입 |
|---|---|---|
| agent_profiles | 프로필 설정 | 일반 |
| documents | 문서 메타데이터 | 일반 |
| document_chunks | 벡터+FTS+trgm | 일반 |
| facts | 구조화된 팩트 | 일반 |
| conversation_sessions | 대화 세션 | 일반 (영속) |
| cache_entries | 캐시 | UNLOGGED |
| job_queue | 작업 큐 | 일반 |
| workflow_states | 워크플로우 상태 | 일반 (영속) |

## 명령어

```bash
# 환경 구축
pip install -e ".[dev,local]"
docker compose up -d postgres

# DB 마이그레이션
alembic upgrade head

# 개발 서버
uvicorn src.main:app --reload --port 8000

# 테스트
pytest tests/ -x -v

# 전체 실행 (Docker)
docker compose up -d
```

## API 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| GET | /api/health | 헬스체크 |
| GET | /api/profiles | 프로필 목록 |
| POST | /api/chat | 챗봇 (비스트리밍) |
| POST | /api/chat/stream | 챗봇 (SSE 스트리밍) |
| POST | /api/documents/ingest | 문서 수집 |

## 코딩 컨벤션

- 파일명: snake_case
- 주석/커밋: 한국어
- 커밋: conventional commits (`feat:`, `fix:`, `refactor:`)
- 테스트: pytest, asyncio_mode=auto
- 타입: dataclass (frozen=True) + Protocol
- 환경변수: AIP_ prefix

## 구현 로드맵

```
Step 1: 플랫폼 MVP (현재)
  Gateway → Profile → Router → Agent → RAG Tool → 답변

Step 2: 안정화
  Domain Layer + Safety Guard + Observability

Step 3: 비즈니스 자동화
  Workflow Engine + Hybrid 모드 + 개인화

Step 4: 확장
  MCP Tools + Pipeline 고도화 + Experiment Layer
```
