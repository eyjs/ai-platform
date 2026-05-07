# AI Platform System Architecture

**최종 업데이트: 2026-03-27**

## Overview

Profile 기반 범용 AI 에이전트 플랫폼. Agent는 하나(Universal Agent), 행동은 Profile이 결정.
106 files, 11.8K LOC, 368 tests.

## System Flow

```
Request
  |
  v
[C1 Gateway] -- 인증(JWT/API Key), Rate Limiting, CORS
  |
  v
[C2 Orchestrator] -- 프로필 선택 (어떤 도메인 전문가에게 보낼지)
  |  Tier 1: 임베딩 유사도 (~100ms, 다국어, 의도-능력 매칭)
  |  Tier 1-B: 패턴 매칭 (폴백)
  |  Tier 2: 키워드 스코어링
  |  Tier 3: LLM Function Calling (최후 수단)
  |
  v
[C3 Router 4-Layer] -- 실행 전략 결정 (어떻게 답변할지)
  |  L0: Context Resolver (대명사 해소, 패턴 감지 -> LLM 위임)
  |  L1: Intent Classifier (QuestionType 분류, locale 패턴)
  |  L2: Mode Selector (deterministic/agentic/workflow, 트리거 전수 비교)
  |  L3: Strategy Builder (ExecutionPlan 조립, PII 새니타이징)
  |
  v
[C4 Agent] -- LangGraph 파이프라인 실행
  |  route_by_rag -> execute_tools -> graph_enrich -> generate -> guardrails
  |  Thinking 분리: <think> 블록 -> SSE trace / 답변 -> SSE token
  |
  v
[C6 Tool System] -- RAG 5-Layer Pipeline
  |  L1: Adaptive Query Expansion (probe -> 조건부 LLM 확장)
  |  L2: Noise Filter (score gap 기반)
  |  L3: Neighbor Expansion (인접 chunk)
  |  L4: 3-Tier Reranking (CrossEncoder + score fusion, Tier1+Tier2 보충)
  |  L5: Result Guard (PII masking, locale 패턴)
  |
  v
[C9 Safety Guard Chain] -- 답변 검증
  |  FaithfulnessGuard (숫자 bare number 완화, 인용, co-occurrence, LLM deep eval)
  |  PIIFilterGuard (locale 기반 패턴)
  |  ResponsePolicyGuard (strict/balanced)
  |
  v
[SSE Streaming] -- event:trace (thinking/tool) + event:token (delta JSON) + event:done
```

## Layer Dependency Rule

```
Gateway -> Orchestrator -> Router -> Agent -> Tool -> Domain/Infrastructure
                                                   -> Safety
```
단방향만 허용. 역방향 참조 금지.

## 3-Layer Config System

```
Platform Settings (config.py)      -- 인프라, 시크릿, 임계값 (환경변수 AIP_ prefix)
Locale Bundle (locale/ko.yaml)     -- 언어별 문자열, 패턴, PII 규칙 (YAML)
Profile YAML (seeds/profiles/)     -- 도메인 행동, system_prompt, 도구, 가드레일
```

하드코딩 제로. 프롬프트/메시지/패턴/PII 규칙 전부 locale YAML로 외부화.

## 13 Components

| # | Component | Files | Role |
|---|-----------|-------|------|
| C1 | Gateway | 8 | 인증, SSE, Rate Limiting, CORS |
| C2 | Orchestrator | 8 | 임베딩 프로필 라우팅 + 3-Tier 폴백 + 핸드오프 |
| C3 | Router | 7 | 4-Layer 질문 분석 (Context->Intent->Mode->Strategy) |
| C4 | Agent | 10 | LangGraph (deterministic/agentic), thinking 분리 |
| C5 | Workflow | 5 | 절차 기반 대화, 뒤로가기/이탈 |
| C6 | Tool System | 11 | Registry + Permission + Scope 주입 + RAG 5-Layer |
| C7 | Domain | 5 | AgentProfile, SearchScope, SSOT 모델 |
| C8 | Infrastructure | 26 | PostgreSQL 단일 스택, Provider Factory, Memory |
| C9 | Safety | 5 | FaithfulnessGuard, PIIFilter, ResponsePolicy |
| C10 | Pipeline | 4 | Parse->Chunk->Embed->Index (비동기 Job Queue) |
| C11 | Observability | 4 | 구조화 JSON 로깅, RequestTrace |
| C12 | Locale | 2 | 다국어 문자열/패턴/PII (ko.yaml->LocaleBundle) |
| C13 | Services | 2 | KMS 지식그래프 클라이언트 |

## Orchestrator: Embedding-based Profile Routing

프로필의 system_prompt, description, domain_scopes를 임베딩하여 "능력 기술"로 사용.
질문 임베딩과 cosine similarity로 가장 적합한 프로필 자동 선택.

- 대표 질문 관리 불필요 -- 프로필 YAML만 잘 쓰면 라우팅 자동
- 다국어 지원 (BGE-m3-ko)
- threshold 미달 시 패턴 -> 키워드 -> LLM 순차 폴백
- ambiguous 감지 (1위-2위 gap < 0.03) -> 폴백

## Hybrid Search

```
pgvector (ANN) + tsvector (FTS) + pg_trgm (fuzzy)
    |               |                |
    +----------- RRF 병합 ----------+
                    |
              RRF score (K=60)
```

## MLX Server Layout (Apple Silicon M1 Max 64GB)

| Port | Model | Role | VRAM |
|------|-------|------|------|
| 8102 | bge-reranker-v2-m3 | Reranker (MPS) | ~1GB |
| 8103 | BGE-m3-ko (1024d) | Embedding (MPS) | ~1.5GB |
| 8104 | Qwen3-14B-4bit | Main LLM (최종 응답 생성) | ~8GB |
| 8105 | Qwen3-8B-4bit | Router/Orchestrator LLM | ~5GB |
| 8106 | Qwen2.5-7B-4bit | Other system | ~4GB |
| **Total** | | | **~19.5GB** |

## SSE Protocol

```
event: trace     -- 파이프라인 추적 (tool, thinking 등)
data: {"step": "thinking", "content": "..."}

event: token     -- 답변 토큰
data: {"delta": "대인배상I의 보상한도는"}

event: done      -- 완료 + 소스
data: {"answer": "...", "sources": [...], "confidence": null}
```

## DB Schema (PostgreSQL Only)

| Table | Purpose | Type |
|-------|---------|------|
| agent_profiles | 프로필 설정 | Normal |
| documents | 문서 메타데이터 | Normal |
| document_chunks | 벡터+FTS+trgm | Normal |
| facts | 구조화된 팩트 | Normal |
| conversation_sessions | 대화 세션 | Normal (persistent) |
| cache_entries | 캐시 | UNLOGGED |
| job_queue | 작업 큐 (SKIP LOCKED) | Normal |
| workflow_states | 워크플로우 상태 | Normal (persistent) |
| api_keys | API Key 관리 | Normal |

## Design Principles

1. **Profile = Chatbot** -- YAML 추가 = 새 챗봇. 코드 변경 0줄.
2. **No Agent Sprawl** -- Agent는 하나. 행동은 Profile이 결정.
3. **Strict Layer Separation** -- 단방향. 역방향 금지.
4. **PostgreSQL Single Stack** -- 벡터/캐시/세션/큐 모두 PostgreSQL.
5. **Tool Permission as Security** -- Profile.tools에 없는 도구는 LLM이 모른다.
6. **Scope Injection** -- Tool은 어떤 봇에서 호출됐는지 모른다.
7. **Locale as Config** -- 모든 언어 의존 문자열은 YAML 외부화.
8. **Capability-based Routing** -- 프로필 능력 기술 임베딩으로 의미 기반 라우팅.
