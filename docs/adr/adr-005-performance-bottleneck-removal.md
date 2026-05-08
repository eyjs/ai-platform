# ADR-005: 전방위 성능 병목 제거

**Date**: 2026-05-07
**Status**: Accepted
**Deciders**: Pipeline Orchestrator + Planner + Reviewer

## Context

매 요청마다 agentic graph 재빌드(300-800ms), 순차 DB 쿼리, Python-level JSONB 전체 로드, DB 풀 과다 할당 등 전방위적 성능 병목이 존재. 추가 인프라(Redis 등) 도입 없이 코드 레벨 구조 최적화만으로 지연을 줄인다.

## Decision

7개 태스크를 4 Phase로 나누어 순차/병렬 실행.

### T1. Agentic Graph LRU Cache

`(profile_id, frozenset(tool_names))` 복합 키로 LangGraph 인스턴스를 `functools.lru_cache`에 캐싱. TTL 2시간. 동일 프로필 두 번째 요청부터 graph 재빌드 제거.

**Alternative**: Redis 기반 분산 캐시. PostgreSQL 단일 스택 원칙 위반으로 기각.

### T2. RAG Neighbor Expansion + Multi-Query 병렬화

- Neighbor expansion: chunk당 개별 `SELECT` → `WHERE id = ANY($1)` 단일 IN 쿼리
- Multi-query search: 순차 실행 → `asyncio.gather` 병렬 실행

**Alternative**: DB-side function으로 통합. 복잡도 대비 이점 부족으로 기각.

### T3. Hybrid Search 서브쿼리 병렬화

vector/FTS/trgm 3개 서브쿼리를 `asyncio.gather`로 동시 실행. RRF merge 로직은 기존 방식 유지. Tool Protocol 외부 인터페이스 변경 없음.

### T4. Workflow Engine 이중 저장 제거

`resume()` 경로에서 `_save_session` 2회 호출 → 1회로 통합. 불필요한 DB write 제거.

### T5. DB Pool 파라미터 명시적 설정

asyncpg Pool과 SQLAlchemy AsyncEngine이 동일 DB에 별도 풀 유지. 완전 통합은 영향 범위가 넓어 이번에는 SQLAlchemy 측 풀 파라미터를 명시적으로 설정하여 안정성 확보.

- `pool_size=5`, `max_overflow=10`, `pool_timeout=30`, `pool_recycle=3600`
- asyncpg: `pg_pool_min` 5→2, `pg_pool_max` 50→20

**Alternative**: asyncpg Pool 제거하고 SQLAlchemy로 완전 통합. 기존 asyncpg 직접 사용 코드 전수 수정 필요하여 별도 트랙으로 연기.

### T6. Session Memory SQL-level JSONB Slice

Python에서 전체 JSONB 로드 후 슬라이스 → DB에서 `jsonb_array_elements WITH ORDINALITY`로 마지막 N개만 추출. 전송량 최소화.

**Alternative**: 턴을 별도 테이블로 분리 (append-only). 스키마 변경 + 마이그레이션 부담으로 기각. SQL-level slice가 스키마 변경 없이 동일 효과.

### T7. Prompt Caching

- OpenAI: system prompt에 `cache_control` 적용
- Anthropic: `cache_control: {"type": "ephemeral"}` 블록 적용
- Provider 추상화 레이어(`BaseLLMProvider.supports_caching`)에서만 처리, 상위 레이어 변경 없음

## Consequences

### Positive
- Graph 재빌드 제거로 요청당 300-800ms 절감
- Hybrid search 병렬화로 검색 지연 ~40% 감소
- Prompt caching으로 LLM 비용 절감 + 응답 지연 감소
- DB 풀 안정성 향상 (과다 할당 제거)
- 850 tests passed, 1 skipped (회귀 없음)

### Negative
- LRU cache는 단일 워커 한정 — 멀티 워커 시 cross-worker 무효화 별도 설계 필요
- asyncpg + SQLAlchemy 이중 풀 구조는 여전히 존재 (완전 통합은 별도 트랙)

### Risks
- Graph cache 무효화 누락 시 stale graph 사용 가능 (Profile YAML 변경 빈도가 낮아 리스크 낮음)
- `asyncio.gather`에서 하나의 서브쿼리 실패 시 전체 검색 실패 (개별 예외 처리로 완화)
