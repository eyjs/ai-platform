# apps/api — Universal Agent Runtime (Python / FastAPI)

> 이 파일은 `apps/api` 전용 컨텍스트다. 워크스페이스 전역 규칙은 루트 `CLAUDE.md` 참조. 이 문서는 자급자족(self-contained)이며, 에이전트가 이 파일만 읽어도 apps/api 작업이 가능하다.

## 역할

Profile 기반 범용 AI 에이전트 런타임. ChatGPT GPTs처럼 설정(YAML)만으로 도메인별 AI 챗봇을 생성/운영한다. **Agent는 하나, 행동은 Profile이 결정한다.**

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

- 각 레이어는 상위/하위의 내부 구현을 알지 않는다.
- 레이어 간 의존은 단방향: **Gateway → Router → Agent → Tool**. 역방향 참조 금지.
- Orchestrator 레이어(있다면)는 Router "앞"에서 Profile 선택 전처리 역할이다. Router를 감싸는 조정자가 **아니다.**

### 3. 인프라 최소화 — PostgreSQL 단일 스택

| 기존 (Redis) | 대체 (PostgreSQL) |
|---|---|
| 세션 캐시 | `UNLOGGED TABLE` + JSONB + `expires_at` |
| Workflow State | 일반 테이블 + JSONB (영속) |
| Pub/Sub 이벤트 | `LISTEN/NOTIFY` |
| 작업 큐 | `SELECT FOR UPDATE SKIP LOCKED` |
| TTL 만료 정리 | 주기적 `DELETE` (asyncio task) |

### 4. Tool Permission이 보안의 핵심
- `tools` 리스트에 없는 도구는 LLM이 **존재를 모른다**
- Tool 내부에서 "어떤 봇에서 호출됐는지" 알면 안 된다 → scope/context를 받아서 동작

## 절대 규칙 6개

1. **Profile YAML 추가만으로 새 챗봇이 동작해야 한다.** 코드 변경 필요하면 설계가 틀린 것
2. **Tool은 scope/context를 받아서 동작한다.** Tool 내부에서 봇 식별 금지
3. **Router는 Profile을 읽을 뿐 하드코딩하지 않는다.** `if profile == "..."` 코드 금지
4. **PostgreSQL 단일 스택.** Redis, Elasticsearch 등 추가 인프라 도입 금지
5. **레이어 간 의존은 단방향이다.** Gateway → Router → Agent → Tool
6. **ai-worker 자산은 복사 후 확장한다.** import로 참조하지 않는다

## 12 컴포넌트

```
C1.  AI Gateway          — 인증, Profile 로딩, UserContext, SSE
C2.  AI Router           — 4-Layer (Context → Intent → Mode → Strategy)
C2-A AgentProfile        — 챗봇 설정 단위 (= GPTs)
C3.  Universal Agent     — 결정론적 파이프라인, Profile 기반 동작
C4.  Workflow Engine     — 절차 기반 대화
C5.  Tool System         — Registry + Permission + Scope 주입
C6.  Domain Layer        — SearchScope, Context Builder
C7.  Infrastructure      — PostgreSQL 단일 (벡터/캐시/큐/세션)
C8.  Safety Guard        — Faithfulness, PII, ResponsePolicy (동적 체인)
C9.  Observability       — 라우팅 로그, Tool latency
C10. Knowledge Pipeline  — Parse → Chunk → Embed → Index
C11. Experiment Layer    — 향후
C12. Memory System       — Short/Session (PostgreSQL 기반)
```

## Router 4-Layer

```
Layer 0: Context Resolver  — 대명사 해소 (ChainResolver: Pattern → LLM)
Layer 1: Intent Classifier — 패턴매칭 → QuestionType 8종 분류
Layer 2: Mode Selector     — Profile.mode + Intent → agentic/workflow
Layer 3: Strategy Builder  — ExecutionPlan 생성 (scope, tools, prompt, guardrails)
```

## 기술 스택

| 영역 | 기술 |
|---|---|
| 언어 | Python 3.11+ |
| 프레임워크 | FastAPI |
| 패키징 | hatchling (`pyproject.toml`) |
| DB | PostgreSQL 16 + pgvector + pg_trgm |
| 마이그레이션 | alembic |
| 워크플로우 | LangGraph |
| 임베딩 | sentence-transformers (dev) / OpenAI (prod) |
| LLM | Ollama (dev) / OpenAI (prod) |
| 리랭커 | CrossEncoder (dev) / HTTP (prod) |
| 스트리밍 | SSE (sse-starlette) |
| 테스트 | pytest (asyncio_mode=auto) |

## 디렉토리 구조

```
apps/api/
├── CLAUDE.md             # ← 이 파일
├── pyproject.toml
├── alembic.ini
├── alembic/versions/     # 마이그레이션
├── src/
│   ├── main.py           # FastAPI + lifespan
│   ├── config.py         # Settings (AIP_ prefix)
│   ├── gateway/          # C1: 인증, SSE, 엔드포인트
│   ├── router/           # C2: 4-Layer Router
│   ├── agent/            # C3: Universal Agent, Profile
│   ├── workflow/         # C4: Workflow Engine
│   ├── tools/            # C5: Tool Protocol, Registry
│   │   └── internal/     # 내장 도구 (rag_search, fact_lookup)
│   ├── domain/           # C6: Context Builder, SearchScope
│   ├── infrastructure/   # C7: PostgreSQL 기반 전체 인프라
│   │   ├── providers/    # LLM/Embedding/Reranker
│   │   └── memory/       # Session, Cache (PostgreSQL)
│   ├── safety/           # C8: Guardrail Chain
│   ├── observability/    # C9: 메트릭, 로그
│   └── pipeline/         # C10: Knowledge Pipeline
├── seeds/
│   ├── profiles/         # Profile YAML
│   └── workflows/        # Workflow YAML
├── scripts/
├── static/
└── tests/
```

## DB 스키마 (PostgreSQL only)

| 테이블 | 용도 | 타입 |
|---|---|---|
| `agent_profiles` | 프로필 설정 | 일반 |
| `documents` | 문서 메타데이터 | 일반 |
| `document_chunks` | 벡터 + FTS + trgm | 일반 |
| `facts` | 구조화된 팩트 | 일반 |
| `conversation_sessions` | 대화 세션 | 일반 (영속) |
| `cache_entries` | 캐시 | UNLOGGED |
| `job_queue` | 작업 큐 | 일반 |
| `workflow_states` | 워크플로우 상태 | 일반 (영속) |

## Quick Start

```bash
cd apps/api
pip install -e ".[dev,local]"
docker compose -f ../../docker-compose.yml up -d postgres
alembic upgrade head
uvicorn src.main:app --reload --port 8000
```

## 명령어

```bash
# 의존성 설치 (dev 포함)
pip install -e ".[dev,local]"           # 로컬 개발 (sentence-transformers 포함)
pip install -e ".[dev,ollama]"          # Ollama LLM
pip install -e ".[dev,openai]"          # OpenAI LLM

# DB 마이그레이션
alembic upgrade head
alembic revision --autogenerate -m "메시지"

# 개발 서버
uvicorn src.main:app --reload --port 8000

# 테스트
pytest tests/ -x -v
pytest tests/ --cov=src --cov-report=term-missing
```

## API 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/health` | 헬스체크 |
| GET | `/api/profiles` | 프로필 목록 (공개) |
| POST | `/api/chat` | 챗봇 (비스트리밍, JWT/API Key 필수) |
| POST | `/api/chat/stream` | 챗봇 (SSE 스트리밍, JWT/API Key 필수) |
| POST | `/api/documents/ingest` | 문서 수집 (JWT/API Key 필수) |

## 인증

- JWT (HS256) + API Key
- 헤더: `Authorization: Bearer <token>` 또는 `X-API-Key: <key>`
- JWT payload: `sub` (user_id), `role`, `security_level_max`, `user_type`
- `AIP_JWT_SECRET`은 **apps/bff와 공유**한다 (bff가 발급한 토큰으로 api 인증 통과)

## 환경변수

모두 `AIP_` prefix:

| 변수 | 예시 | 설명 |
|---|---|---|
| `AIP_DATABASE_URL` | `postgresql+asyncpg://user:pass@localhost:5432/aip` | DB 접속 |
| `AIP_LLM_PROVIDER` | `ollama` / `openai` | LLM 프로바이더 |
| `AIP_EMBEDDING_PROVIDER` | `local` / `openai` | 임베딩 프로바이더 |
| `AIP_LLM_API_KEY` | `sk-...` | OpenAI 사용 시 |
| `AIP_JWT_SECRET` | (공유) | bff와 동일 값 |

## 코딩 컨벤션

- **파일명**: `snake_case`
- **주석/커밋**: 한국어 허용, 영어 혼용 가능
- **커밋**: conventional commits (`feat(api):`, `fix(api):`, `refactor(api):`)
- **테스트**: `pytest`, `asyncio_mode=auto`
- **타입**: `dataclass(frozen=True)` + `Protocol` 조합. 가변 mutable 상태 지양
- **불변성**: 항상 새 객체 생성, 기존 객체 변경 금지
- **에러 핸들링**: 조용히 삼키지 않기, 컨텍스트 포함, 사용자 대면 메시지는 친화적으로

## 이 앱에서 하면 안 되는 것

1. ❌ **Redis, Elasticsearch, MongoDB 등 추가 인프라 도입** — PostgreSQL 단일 스택 원칙 위반
2. ❌ **Router에서 Profile 하드코딩** — `if profile_id == "customer_support": ...` 같은 코드 금지
3. ❌ **Tool 내부에서 봇 식별** — Tool은 scope/context만 받는다. `if bot_name == ...` 금지
4. ❌ **레이어 역방향 참조** — Tool이 Agent를 import, Agent가 Router를 import 금지
5. ❌ **apps/bff, apps/frontend 코드 참조** — 언어가 다르기도 하고, 경계 위반
6. ❌ **`any` 타입 대용(`Any`) 남용** — 구체 타입 또는 Protocol 사용
7. ❌ **하드코딩 시크릿** — `sk-...`, DB 비밀번호 등 코드에 직접 넣기 금지. 항상 `AIP_` env
8. ❌ **새 챗봇을 위해 코드 수정** — Profile YAML만으로 동작해야 한다
9. ❌ **`ai-worker`에서 코드 import** — 필요하면 복사 후 확장
