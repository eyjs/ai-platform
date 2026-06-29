# ADR-014 — 클린 아키텍처 정합 (god 파일 분할 + 레이어 단방향 + services 재편)

- **상태**: 채택 (2026-06-29)
- **결정자**: 파이프라인 (Planner/Reviewer 합의)
- **커밋**: `d0db65a..307aeb8` — 트랙 `refactor/clean-arch-alignment`
- **관련 ADR**: [ADR-013 LangGraph Workflow Engine 이전](adr-013-langgraph-workflow-engine-migration.md)

---

## 맥락

`apps/api` 폴더구조 검토(2026-06-26) 결과 "어떤 개발자가 와도 분석 가능" 측면에서 B+ 평가. 레이어=디렉토리 구조와 문서는 우수하나 3개 구조적 흠이 식별됐다.

**흠 1 — god 파일 2개 (src 최대 800 LOC 위반)**

- `agent/graph_executor.py` (917 LOC): 워크플로우·결정론적·에이전틱 모드 실행 코드가 단일 파일에 혼재.
- `infrastructure/vector_store.py` (859 LOC): 연결/CRUD 코어와 16개 검색 파이프라인 메서드가 혼재.

**흠 2 — 레이어 upward import (단방향 위반)**

- `agent/graph_executor.py:23` — `from src.router.execution_plan import ExecutionPlan`
- `agent/graph_executor.py:24` — `from src.router.graph_cache import GraphCache`
- `agent/state.py:12`, `agent/nodes.py:17` — 동일 경로 참조

`agent/`가 `router/`를 import하는 구조는 **Gateway → Router → Agent → Tool** 단방향 원칙 위반이며, 신규 개발자에게 "왜 거꾸로?" 혼란을 유발한다.

**흠 3 — `services/` 캐치올 (철학 긴장)**

범용 cross-cutting 모듈(`feedback_*`, `kms_*`, `domain_mapping`)과 saju consumer 전용 모듈(`fortune_prompts`, `fortune_service`, `myo_play_prompts`, `saju_report_service`)이 동일 디렉토리에 혼재. "Agent 하나, Profile이 행동 결정" 철학(범용 플랫폼)과 긴장 관계.

본 트랙은 **행동 보존(behavior-preserving) 리팩터**다. 순수 이동 + import 갱신 + 위임만. 응답·라우팅·RAG 결과 변경 0, 회귀 0이 불변 가드.

---

## 결정

### 4개 영역 리팩터

**영역 A (T1): ExecutionPlan → domain, GraphCache → agent**

- `router/execution_plan.py` → `domain/execution_plan.py` (R100, 0 변경)
- `router/graph_cache.py` → `agent/graph_cache.py` (R100, 0 변경)
- import 갱신: src 8파일 + test 20파일(plan 추정 17 → 실측 20)

**영역 B (T2): vector_store Mixin 합성 분할**

- `infrastructure/vector_store.py` (859) → `vector_store.py` (404, 연결/CRUD 코어) + `infrastructure/vector_search.py` (474, `VectorSearchMixin`: 검색 파이프라인 16 메서드)
- `VectorStore(VectorSearchMixin, AbstractVectorStore)` 다중상속. MRO로 추상 15개 전부 충족.
- 상수(`HYBRID_CANDIDATE_MULTIPLIER`, `TRIGRAM_FALLBACK_THRESHOLD` 등)는 `vector_search.py`로 이동 후 `vector_store.py`에서 re-export → 테스트 무수정 통과.

**영역 C (T3): graph_executor 모드별 Mixin 합성 분할**

- `agent/graph_executor.py` (917) → 코어(178, `__init__`/디스패치/`invalidate_graph_cache`) + `agent/executors/{_helpers.py 67, workflow_executor.py 156, deterministic_executor.py 177, agentic_executor.py 413}`
- `GraphExecutor(WorkflowExecutorMixin, DeterministicExecutorMixin, AgenticExecutorMixin)` 합성. `self.*` 호출 MRO로 해소, 본문 byte-identical.
- 공유 헬퍼(`_content_to_text`, `_extract_faithfulness_score`, `_build_agentic_user_turn`) → `executors/_helpers.py` 분리.
- monkeypatch 경로 갱신: `graph_executor` → `executors.agentic_executor`.

**영역 D (T4): services saju 격리**

- saju 전용 4모듈(`fortune_prompts`, `fortune_service`, `myo_play_prompts`, `saju_report_service`) → `services/consumers/saju/`
- `domain_mapping`, `feedback_*`, `kms_*`, `response_cache*`는 범용 서비스로 `services/` 잔류.
- import 갱신 5곳: `bootstrap.py`(2곳), `services/fortune_service.py`(2곳), `test_saju_report_integration.py`(1곳).

### 핵심 설계 결정

**D1 — execution_plan을 파일 통째로 domain으로 이동 (re-export shim 미설치)**

`ExecutionPlan`의 필드 타입(`question_type: QuestionType`, `strategy: QuestionStrategy`)이 같은 파일에 정의된 타입을 참조한다. `ExecutionPlan`만 옮기고 `QuestionType`을 `router/`에 남기면 `domain → router` upward import가 발생(레이어 역참조). 따라서 4개 클래스를 한 파일로 통째 이동해야 단방향이 닫힌다.

`router/`에 re-export shim을 남기지 않는다. shim은 "왜 두 곳에 있지?" 혼란을 남겨 단방향 가시화 목적과 충돌하며, 미갱신 importer는 import 에러로 즉시·시끄럽게 실패(안전한 실패 모드)한다. src 8파일 + test 20파일 전수 직접 갱신으로 처리.

**D2 — GraphCache 위치 = `agent/graph_cache.py` (infrastructure/ 아님)**

실측 결과 모듈 importer가 `src/agent/graph_executor.py` 단 1곳이다.
- `workflow/graph_builder.py:201`은 로그 이벤트명 문자열(`"workflow_graph_cache_invalidated"`) — import 아님.
- `gateway/admin_router.py`는 `state.agent.invalidate_graph_cache(...)` 메서드 호출 — import 아님.

유일 소비자가 agent이므로 `agent/graph_cache.py`가 응집도 최상. `infrastructure/`는 이 앱의 "PostgreSQL 단일 스택" 테마 레이어인데, `GraphCache`는 컴파일된 LangGraph 객체를 담는 in-process threading TTL 캐시로 PostgreSQL 인프라와 무관하다. `domain/`은 가변 상태+Lock 보유 객체에 부적합.

**D3 — Mixin 합성 (함수형 분리 대신)**

`vector_store` 및 `graph_executor` 분할에서 함수형(self/conn 전달) 대신 Mixin 합성을 선택한다. 함수형은 모든 호출부 시그니처를 변경해 diff/리스크가 커진다. Mixin 합성은 `self._*` 호출이 MRO로 해소되어 **메서드 본문 byte-identical** — 행동 보존이 1순위 가드인 본 트랙에서 호출부 무변경이 결정적 레버다.

---

## 결과

### DoD 달성표

| DoD | 내용 | 검증 | 상태 |
|-----|------|------|------|
| 1 | src(.py) >800 LOC = 0 | `find src -name '*.py' \| xargs wc -l \| awk '$1>800'` = 빈 결과 (최대 graph_builder 771) | PASS |
| 2 | agent/의 router upward import = 0 | `grep -rn "from src.router" src/agent/` = 0 | PASS |
| 3 | services/에 saju 전용 로직 없음 | `ls src/services/*.py` → saju 모듈 없음; `consumers/saju/`에 4모듈 | PASS |
| 4 | 전체 테스트 그린 (회귀 0) | `pytest tests/ -q` → 1390 passed, 14 skipped | PASS |

### LOC before/after

| 파일 | before | after |
|------|--------|-------|
| `agent/graph_executor.py` | 917 | 178 (코어) |
| `agent/executors/agentic_executor.py` | — | 413 |
| `agent/executors/deterministic_executor.py` | — | 177 |
| `agent/executors/workflow_executor.py` | — | 156 |
| `agent/executors/_helpers.py` | — | 67 |
| `infrastructure/vector_store.py` | 859 | 404 |
| `infrastructure/vector_search.py` | — | 474 |

신규 파일 합계(분할 산출물 포함): 44 files changed, 1389 insertions(+), 1296 deletions(−). 순 LOC 변동은 modest(rename R100 다수), 실질 이득은 질적(단방향 가시화, god 0, consumer 격리).

### 코드 리뷰 결과 (AST 본문 대조)

- T2: 함수 30/30 보존, 본문 변경 0
- T3: 함수 18/18 보존, 본문 변경 0
- 테스트 diff: import/patch 경로 갱신만, assertion/skip/로직 변경 0건

### 비범위 및 후속

- `tools/internal/`의 saju 전용 모듈(`saju_report_*`, `saju_*_prompts`)은 본 트랙 범위 밖(DoD#3은 `services/` 한정). 별도 consumer 격리 트랙 권장.
- GraphCache 위치가 team-lead 초기 제안(`infrastructure/`)과 다르게 `agent/`로 확정됨. 근거(D2)는 코드 리뷰에서 수용 판정.
