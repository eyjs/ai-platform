# AI Platform

**어떤 웹사이트든 AI 챗봇을 붙일 수 있는 범용 플랫폼.**

API Key 하나와 Profile 설정만으로 도메인별 AI 챗봇을 생성한다.
외부 시스템(DMS, CMS, 사내 포탈 등)에서 문서를 수집하고, 해당 문서 기반으로 질의응답하는 RAG 챗봇을 제공한다.

```
외부 웹사이트  ──  <script src="ai-platform/widget.js">  ──  챗봇 동작
                        |
                   API Key + Profile 헤더
                        |
                   AI Platform (이 프로젝트)
                        |
                   문서 수집 API  ←──  KMS, DMS, CMS 등 외부 시스템
```

## How It Works

```
1. Profile 생성    "캠핑장 예약 안내 챗봇 만들어줘"  →  camping-reservation.yaml
2. 문서 수집       캠핑장 이용약관, 요금표, FAQ 등   →  POST /api/documents/ingest
3. 챗봇 동작       "글램핑 2박 요금이 얼마예요?"     →  문서 기반 RAG 답변
```

Profile을 만들고 문서를 밀어넣으면 끝. 코드 변경 없이 새 도메인 챗봇이 바로 동작한다.

## Use Cases

- **캠핑장 예약 안내** -- 이용약관, 요금표, FAQ 문서만 넣으면 예약 관련 질의응답 챗봇 완성.
- **보험 상품 상담** -- 보험 약관 PDF를 수집하면 보장 내용, 보험금 한도 등 답변하는 챗봇.
- **사내 IT 헬프데스크** -- 사내 매뉴얼, 장애 대응 가이드를 넣으면 직원용 Q&A 챗봇.
- **외부 웹사이트 위젯** -- JS 스크립트 한 줄로 챗봇 삽입. ai-platform을 모르는 시스템에서도 동작.
- **멀티 테넌트** -- API Key로 고객 식별, `X-Chatbot-Profile` 헤더로 봇 선택, domain_scope로 문서 격리.

## Features

- **Profile = Chatbot** -- Admin UI 또는 YAML로 챗봇 생성. 코드 변경 0줄.
- **Admin UI** -- 관리자 페이지에서 챗봇 프로필 CRUD + KMS 도메인 연동.
- **KMS Integration** -- KMS 도메인 목록 프록시 조회 (Internal Key 인증, Docker 내부 통신).
- **Embed Anywhere** -- API Key + 헤더만으로 어떤 웹사이트든 챗봇 연동.
- **4-Layer Router** -- 대명사 해소 > 의도 분류 > 모드 선택 > 실행 계획 조립
- **Hybrid Search** -- pgvector(ANN) + tsvector(FTS) + pg_trgm(fuzzy) + RRF 병합
- **Safety Guard Chain** -- Faithfulness, PII Filter, Response Policy (동적 체인)
- **Workflow Engine** -- 절차 기반 대화 (예약 접수, 상담 안내 등). Admin API로 생성/관리.
- **SSE Streaming** -- 토큰 단위 스트리밍 + 추론 과정(trace) 실시간 전송
- **PostgreSQL Only** -- 벡터, 캐시, 세션, 큐 모두 PostgreSQL 단일 스택. Redis 불필요.
- **Document Ingestion API** -- 외부 시스템이 REST API로 문서를 밀어넣으면 자동 파싱/청킹/임베딩.

## Architecture

```
Gateway  -->  Router  -->  Agent  -->  Tool
  |             |            |          |
 인증        4-Layer      Pipeline     RAG Search
 Profile     분류/전략    LLM 생성     Fact Lookup
 SSE         Plan 조립   Guardrail    (scope 주입)
```

### Router 4-Layer

| Layer | 이름 | 역할 |
|-------|------|------|
| L0 | Context Resolver | 대명사 해소 (Pattern > LLM 2-tier) |
| L1 | Intent Classifier | QuestionType 8종 분류 |
| L2 | Mode Selector | agentic / workflow 모드 결정 |
| L3 | Strategy Builder | ExecutionPlan 생성 (scope, tools, prompt, guardrails) |

### 12 Components

| # | Component | Description |
|---|-----------|-------------|
| C1 | AI Gateway | 인증, Profile 로딩, SSE 스트리밍 |
| C2 | AI Router | 4-Layer 질문 라우팅 |
| C3 | Universal Agent | 결정론적 RAG 파이프라인 |
| C4 | Workflow Engine | 절차 기반 대화 (예정) |
| C5 | Tool System | Registry + Permission + Scope 주입 |
| C6 | Domain Layer | SearchScope, 공유 모델 |
| C7 | Infrastructure | PostgreSQL 단일 스택 (벡터/캐시/큐/세션) |
| C8 | Safety Guard | Faithfulness, PII, ResponsePolicy |
| C9 | Observability | 구조화 로깅, 추적 |
| C10 | Knowledge Pipeline | Parse > Chunk > Embed > Index |
| C11 | Experiment Layer | A/B 테스트 (예정) |
| C12 | Memory System | Session + Cache (PostgreSQL) |

## Tech Stack

| Area | Development | Production |
|------|-------------|------------|
| Framework | FastAPI | FastAPI |
| Database | PostgreSQL 16 + pgvector | PostgreSQL 16 + pgvector |
| Embedding | sentence-transformers (BGE-m3-ko) | OpenAI text-embedding-3-small |
| LLM | Ollama (qwen3:8b) | OpenAI gpt-4o-mini |
| Reranker | CrossEncoder (BGE-reranker-v2-m3) | HTTP API |
| Streaming | SSE (sse-starlette) | SSE (sse-starlette) |

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 16 with pgvector extension
- Docker & Docker Compose (recommended)

### Run with Docker

```bash
# Clone
git clone https://github.com/eyjs/ai-platform.git
cd ai-platform

# Configure
cp .env.example .env
# Edit .env with your settings

# Start
docker compose up -d

# Verify
curl http://localhost:8000/api/health
```

### Local Development

```bash
# Virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev,local]"

# Start PostgreSQL
docker compose up -d postgres

# Run migrations
alembic upgrade head

# Start dev server
uvicorn src.main:app --reload --port 8000

# Run tests
pytest tests/ -x -v
```

## API

### Data Plane

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/profiles` | List available profiles |
| POST | `/api/chat` | Chat (non-streaming) |
| POST | `/api/chat/stream` | Chat (SSE streaming) |
| POST | `/api/documents/ingest` | Document ingestion |

### Admin (Control Plane)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/admin/profiles` | 프로필 목록 |
| GET | `/api/admin/profiles/{id}` | 프로필 상세 |
| POST | `/api/admin/profiles` | 프로필 생성 |
| PUT | `/api/admin/profiles/{id}` | 프로필 수정 (부분) |
| DELETE | `/api/admin/profiles/{id}` | 프로필 삭제 (soft) |
| GET | `/api/admin/workflows` | 워크플로우 목록 |
| POST | `/api/admin/workflows` | 워크플로우 생성 |
| PUT | `/api/admin/workflows/{id}` | 워크플로우 수정 |
| DELETE | `/api/admin/workflows/{id}` | 워크플로우 삭제 (soft) |
| GET | `/api/admin/kms/domains` | KMS 도메인 프록시 조회 |
| POST | `/api/admin/cache/invalidate` | 캐시 전체 무효화 |

### Chat Example

```bash
# API Key + Profile 헤더로 외부 시스템에서 호출
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -H "X-Chatbot-Profile: camping-reservation" \
  -d '{
    "question": "글램핑 2박 요금이 얼마예요?",
    "session_id": "user-123"
  }'
```

### SSE Streaming

```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "question": "보험 약관 요약해줘",
    "profile_id": "insurance-qa"
  }'
```

### Document Ingestion

```bash
# 외부 DMS/CMS에서 문서를 밀어넣기
curl -X POST http://localhost:8000/api/documents/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "title": "캠핑장 이용약관",
    "content": "1. 체크인 15:00, 체크아웃 11:00...",
    "domain_code": "camping",
    "security_level": "PUBLIC"
  }'
```

## Creating a Profile

### Option 1: Admin UI (recommended)

`http://localhost:8000/static/admin.html` 에서 관리자 페이지에 접속하여 챗봇을 생성한다.

- API Key 설정 (우측 상단)
- "+ New Chatbot" 클릭
- 기본 정보, 모드, 검색 범위(KMS 도메인), 도구, 응답 정책, 메모리 설정
- KMS 연동 시 도메인 체크박스로 검색 범위 자동 설정

### Option 2: YAML Seed

Add a YAML file to `seeds/profiles/`. Example -- camping reservation chatbot:

```yaml
id: camping-reservation
name: Camping Reservation Assistant
description: 캠핑장 예약 안내 챗봇
mode: agentic

system_prompt: |
  당신은 캠핑장 예약 안내 도우미입니다.
  이용약관, 요금표, FAQ 문서를 기반으로 정확하게 답변하세요.
  문서에 없는 내용은 "확인 후 안내드리겠습니다"라고 답변하세요.

tools:
  - rag_search
  - fact_lookup

domain_scopes:
  - domain_code: camping
    security_level_max: PUBLIC

response_policy: balanced

guardrails:
  - faithfulness
  - pii_filter
```

Restart the server -- the new profile is automatically loaded. Documents ingested with `domain_code: camping` are automatically scoped to this profile.

## Project Structure

```
ai-platform/
├── src/
│   ├── main.py               # FastAPI entrypoint + lifespan
│   ├── config.py             # Settings (AIP_ prefix)
│   ├── domain/               # Shared models, enums
│   ├── gateway/              # HTTP endpoints, SSE, auth
│   │   ├── router.py         # Data plane (chat, ingest, health)
│   │   ├── admin_router.py   # Control plane (profile/workflow CRUD)
│   │   ├── auth.py           # JWT / API Key / Origin 인증
│   │   └── streaming.py      # SSE helpers
│   ├── router/               # 4-Layer intent routing
│   ├── agent/                # Universal Agent, Profile, ProfileStore
│   ├── workflow/              # Workflow Engine + Store + Definition
│   ├── tools/                # Tool Protocol + Registry
│   │   └── internal/         # Built-in tools (RAG, Facts)
│   ├── infrastructure/       # PostgreSQL, providers
│   │   ├── providers/        # LLM / Embedding / Reranker
│   │   └── memory/           # Session, Cache
│   ├── safety/               # Guardrail chain
│   ├── observability/        # Structured logging, tracing
│   └── pipeline/             # Document ingestion pipeline
├── static/
│   ├── admin.html            # Admin UI (profile CRUD + KMS 연동)
│   └── chat-widget.html      # Chat widget (SSE streaming)
├── seeds/profiles/           # Profile YAML definitions
├── tests/                    # pytest test suite
├── alembic/                  # DB migrations
├── docker-compose.yml        # PostgreSQL + app
└── pyproject.toml
```

## KMS Integration

ai-platform은 KMS(문서관리 프레임워크)와 연동하여 도메인 정보를 조회한다.

```
KMS (NestJS)                    ai-platform (FastAPI)
──────────────                  ─────────────────────
문서 체계 관리 (SSOT)            AI 프로필 / RAG / 챗봇
도메인/카테고리/문서              검색 / 임베딩 / 생성
        │                               │
        └──── Docker 내부 통신 ──────────┘
             X-Internal-Key (공유 비밀키)
```

| 설정 | 환경변수 | 설명 |
|------|----------|------|
| KMS API URL | `AIP_KMS_API_URL` | 예: `http://kms-api:3000/api` |
| Internal Key | `AIP_KMS_INTERNAL_KEY` | KMS `INTERNAL_KEY`와 동일한 값 |

KMS 미연결 시 Admin UI의 도메인 체크박스가 비활성화되며, 수동 입력으로 대체 가능.

## Design Principles

1. **No Agent Sprawl** -- One Universal Agent runtime. Behavior is driven by Profile configuration.
2. **Strict Layer Separation** -- Gateway > Router > Agent > Tool. No reverse dependencies.
3. **PostgreSQL Single Stack** -- Vector search, caching, sessions, job queue all on PostgreSQL. No Redis.
4. **Tool Permission as Security** -- Tools not listed in a Profile don't exist to the LLM.
5. **Scope Injection** -- Tools receive SearchScope (domain codes, security level) without knowing which bot called them.

## License

MIT
