# AI Platform

Profile 기반 범용 AI 에이전트 플랫폼.
YAML 설정만으로 도메인별 AI 챗봇을 생성하고 운영한다.

```
Agent는 하나 (Universal Agent), 행동은 Profile이 결정한다.
```

## Features

- **Profile = Chatbot** -- YAML 하나 추가하면 새 챗봇이 동작. 코드 변경 0줄.
- **4-Layer Router** -- 대명사 해소 > 의도 분류 > 모드 선택 > 실행 계획 조립
- **Hybrid Search** -- pgvector(ANN) + tsvector(FTS) + pg_trgm(fuzzy) + RRF 병합
- **Safety Guard Chain** -- Faithfulness, PII Filter, Response Policy (동적 체인)
- **SSE Streaming** -- 토큰 단위 스트리밍 + 추론 과정(trace) 실시간 전송
- **PostgreSQL Only** -- 벡터, 캐시, 세션, 큐 모두 PostgreSQL 단일 스택. Redis 불필요.

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

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/profiles` | List available profiles |
| POST | `/api/chat` | Chat (non-streaming) |
| POST | `/api/chat/stream` | Chat (SSE streaming) |
| POST | `/api/documents/ingest` | Document ingestion |

### Chat Example

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "question": "자동차보험 대인배상 한도가 어떻게 되나요?",
    "profile_id": "insurance-qa",
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

## Creating a Profile

Add a YAML file to `seeds/profiles/`:

```yaml
id: my-chatbot
name: My Domain Chatbot
description: Custom chatbot for my domain
mode: agentic

system_prompt: |
  You are a helpful assistant for [domain].
  Answer based on the provided documents.

tools:
  - rag_search
  - fact_lookup

domain_scopes:
  - domain_code: my-domain
    security_level_max: INTERNAL

response_policy: balanced

guardrails:
  - faithfulness
  - pii_filter
```

Restart the server -- the new profile is automatically loaded.

## Project Structure

```
ai-platform/
├── src/
│   ├── main.py               # FastAPI entrypoint + lifespan
│   ├── config.py             # Settings (AIP_ prefix)
│   ├── domain/               # Shared models, enums
│   ├── gateway/              # HTTP endpoints, SSE, auth
│   ├── router/               # 4-Layer intent routing
│   ├── agent/                # Universal Agent, Profile
│   ├── tools/                # Tool Protocol + Registry
│   │   └── internal/         # Built-in tools (RAG, Facts)
│   ├── infrastructure/       # PostgreSQL, providers
│   │   ├── providers/        # LLM / Embedding / Reranker
│   │   └── memory/           # Session, Cache
│   ├── safety/               # Guardrail chain
│   ├── observability/        # Structured logging, tracing
│   └── pipeline/             # Document ingestion pipeline
├── seeds/profiles/           # Profile YAML definitions
├── tests/                    # pytest test suite
├── alembic/                  # DB migrations
├── docker-compose.yml        # PostgreSQL + app
└── pyproject.toml
```

## Design Principles

1. **No Agent Sprawl** -- One Universal Agent runtime. Behavior is driven by Profile configuration.
2. **Strict Layer Separation** -- Gateway > Router > Agent > Tool. No reverse dependencies.
3. **PostgreSQL Single Stack** -- Vector search, caching, sessions, job queue all on PostgreSQL. No Redis.
4. **Tool Permission as Security** -- Tools not listed in a Profile don't exist to the LLM.
5. **Scope Injection** -- Tools receive SearchScope (domain codes, security level) without knowing which bot called them.

## License

MIT
