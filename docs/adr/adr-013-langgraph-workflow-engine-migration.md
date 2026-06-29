# ADR-013 — Workflow Engine을 LangGraph(AsyncPostgresSaver + StateGraph)로 이전

- **상태**: 채택 (2026-06-26)
- **결정자**: 파이프라인 (Planner/Reviewer 합의) + 사용자 컷오버 승인
- **커밋**: `9ffb1d9` (main, T6 컷오버) — 트랙 `68bb205..9ffb1d9`
- **관련 ADR**: [ADR-004 Workflow Action Step](adr-004-workflow-action-step.md)

---

## 맥락

`apps/api`의 Agent 실행(C3)은 이미 LangGraph(`StateGraph`·`create_react_agent`)지만, **절차형 대화(saju_discovery 등)는 자체 Workflow Engine**(`src/workflow/`, ~2123 LOC)이 구동했다. 이 엔진은 LangGraph가 기본 제공하는 기능을 손으로 재구현하고 있었다:

- **상태 영속**: `session_store.py`(193 LOC)가 `workflow_states` 테이블에 save/load/delete/cleanup → LangGraph `PostgresSaver` 체크포인터와 1:1 중복.
- **수동 state-machine 루프 + pause/resume**: `engine.py`의 `_advance_inner`/`_process_current_step`/`resume`.
- 이 영역은 **버그 온상**이었다(2026-06-26 자동 라우팅 escape/stickiness 이슈가 여기서 발생).

`WorkflowSession`(current_step_id·collected·step_history·retry_count·awaiting_callback·callback_response)은 LangGraph 체크포인트 상태에 그대로 매핑된다. 단, 플랫폼 절대규칙 1번("Profile/Workflow **YAML만으로** 동작, 코드 0")과 충돌한다 — LangGraph 그래프는 코드로 정의되기 때문.

## 결정

1. **상태 영속·pause/resume을 LangGraph로 이전.** `AsyncPostgresSaver` 체크포인터(`checkpointer.py` 신규 73 LOC)가 `session_store`를 대체. PG 단일스택 원칙과 호환(`checkpoints`/`checkpoint_writes` 테이블). 내구성 있는 재개·상태 인스펙션·step_history를 체크포인터가 제공.

2. **YAML→StateGraph 동적 빌더로 철학 보존.** `graph_builder.py`(신규 771 LOC) + `graph_state.py`(75 LOC)가 워크플로우 **정의(YAML, `store.py`)를 런타임에 StateGraph로 컴파일**한다. step 타입(message/input/select/confirm/action/dynamic) → 노드, 전이 → conditional edges, user-input step → `interrupt`. **새 워크플로우는 여전히 YAML 추가만**으로 동작.

3. **도메인·DSL은 유지.** `store.py`(정의 로딩)·`step_executors.py`(동작)·`action_client.py`(외부액션)·`context_adapter.py`(도메인)·`StepResult` 계약은 손대지 않음. 교체 대상은 상태영속·state-machine 코어로 **한정**.

4. **비범위.** Router(4-Layer)·Orchestrator(임베딩 라우팅)·RAG 파이프라인·세션 메모리는 그래프 모양이 아니라 LangGraph 부적합 — 손대지 않음.

## 안전 전략 (핵심)

운영 consumer(saju 사주 플로우)가 의존하는 **MEDIUM-HIGH 리스크 코어 교체**였다. 두 겹 방어:

- **AD-1 Feature-flag**: `AIP_WORKFLOW_ENGINE=legacy|langgraph`, **기본 legacy**. 신엔진을 flag 뒤에서 검증 → 동등성 입증 후 컷오버. 롤백 안전.
- **Parity 안전망(T1)**: legacy 동작 스냅샷(S1~S10) + 양 backend parity + durability 테스트. 이전 중 **신엔진 실버그 3개를 정직하게 노출**(xfail+진단, 마스킹 안 함):
  - `_make_message_node` terminal 상태 누락(9건) / `_make_action_node` action_client 미연결(4건) / `_lg_resume` step_id 무시(5건) → 전부 수정(`7a0c33f`).

동등성·내구성 GREEN(flag=langgraph parity 46 passed, flag=legacy 회귀 0) 확인 후 **T6에서 legacy·flag 제거**(`9ffb1d9`), LangGraph 단일 엔진으로 컷오버. 사용자 결정: 양립 시 실사용이 신엔진을 안 거쳐 문제가 안 드러나므로 단일화.

## 결과 / 트레이드오프

- **신규**: `graph_builder.py`(+771)·`graph_state.py`(+75)·`checkpointer.py`(+73). **삭제**: `session_store.py`(−193)·`test_session_store.py`. `engine.py`는 어댑터→단일 langgraph 경로로 대폭 축소. flag 분기(config/bootstrap) 제거.
- **LOC**: 빌더가 전 step 타입을 그래프로 컴파일해야 해 신규가 큼 — **순 LOC 감소는 modest**. 본질 이득은 **질적**: 손으로 짠 state-machine·영속·pause/resume(버그 표면) 제거, 내구성 재개·time-travel·인스펙션 확보.
- **검증**: 전체 워크플로우 스위트 **223 passed**(langgraph 단일). 실제 saju_discovery 워크플로우 정상(그래프 24스텝 컴파일, 유효 응답).
- **의존성**: `langgraph-checkpoint-postgres>=3.0.5,<3.1.0`(상한 필수 — 3.1.0은 langgraph-checkpoint 4.1.0 요구, 설치된 4.0.1과 충돌) + `psycopg[binary,pool]>=3.2.0`(asyncpg와 별도 드라이버).

## 잔여 리스크 / KNOWN GAP

- LangGraph가 **유일 엔진** — 롤백 flag 없음. 운영 회귀 시 `git revert`로 대응(컷오버 전 ec39a90이 flag 병존 상태).
- 체크포인트 테이블이 새로 추가됨(PG) — 용량/정리 정책은 추후 모니터링.
- `_check_escape` 등 일부 도메인 로직은 conditional edge로 잔존(완전 흡수 아님).
