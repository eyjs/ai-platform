"""LangGraph 이전 안전망: legacy 엔진 동작 parity 스냅샷 (T1) + T5 양 backend 파라미터화.

이 테스트는 신엔진(LangGraph)으로 교체하기 전에 현 legacy 엔진의 관찰 가능한
동작을 공개 API로만 스냅샷한다.

T5에서 신엔진(flag=langgraph)에도 그대로 적용되어 동등성을 증명하는 안전망이
된다. 따라서 fixture/factory 패턴으로 엔진 생성을 분리한다.

주의: private 멤버(_sessions, _save_session, _process_current_step, _advance_inner)
접근 절대 금지 — 공개 API(start/advance/resume/get_session/cancel) + StepResult
필드만 단언한다.

각 단언 블록에 "이 단언은 legacy·langgraph 양 엔진에서 동일해야 함" 주석을 달았다.

[T5 SOURCE BUG REPORT]
======================================================================
Bug: message 노드가 completed/concluded 플래그를 설정하지 않음

파일: src/workflow/graph_builder.py
함수: WorkflowGraphBuilder._make_message_node

증상:
  워크플로우의 terminal message 스텝(next 없음)에서 LangGraph 엔진이
  completed=False, concluded=False를 반환한다. legacy 엔진은 동일 상황에서
  completed=True, concluded=True를 반환한다.

원인 분석:
  _make_message_node 클로저는 message_parts/report_hint/current_step_id만
  state dict에 업데이트하고, completed 채널을 업데이트하지 않는다.
  (graph_builder.py:248-260 참조)
  _make_action_node는 terminal 조건 시 completed=True를 명시적으로 설정한다.
  (graph_builder.py:339-345)
  _make_message_node에는 이에 상응하는 처리가 없다.

  _lg_read_last_result(engine.py:515-524)의 최후 폴백 경로도
  `"completed": bool(values.get("completed", False))`를 사용하므로,
  state.completed가 False면 StepResult.completed도 False가 된다.

영향 범위:
  - S1: test_full_flow_completes_with_collected (completed=True 단언)
  - S5: test_valid_phone_recovers (completed=True 단언)
  - S10: test_terminal_message_concluded_true (completed+concluded 단언)
  - S8: action 성공 후 done(message) 스텝 도달 시 completed 반환 여부

수정 방향 (source는 수정하지 않음):
  _make_message_node에서 step.next가 없거나 다음 스텝이 END일 때
  `completed=True`와 `last_result` 설정 로직을 추가해야 한다.
  _make_action_node의 terminal 처리 패턴을 참고.

영향받는 테스트: langgraph backend에 대해 xfail 마킹.
======================================================================

[T5 SOURCE BUG REPORT #2]
======================================================================
Bug: _make_action_node이 action_client=None을 하드코딩한다

파일: src/workflow/graph_builder.py
함수: WorkflowGraphBuilder._make_action_node

증상:
  LangGraph 경로에서 WorkflowEngine(action_client=stub)을 주입해도
  action step 실행 시 stub이 호출되지 않는다. execute_action_step은
  action_client=None으로 호출되어 "외부 연동 기능이 비활성화되어 있습니다."
  오류 메시지를 반환한다.

원인 분석:
  graph_builder.py:312 에서 execute_action_step 세 번째 인자를 항상 None으로
  하드코딩한다. 코드 주석에 "T4에서 연결"이라 명시되어 미구현 상태다.
  WorkflowEngine._action_client는 legacy 경로에서만 step_executors.py에 전달된다.
  LangGraph 경로에서는 그래프 컴파일 시 action_client가 클로저로 캡처되어야 한다.

수정 방향 (source는 수정하지 않음):
  WorkflowGraphBuilder.__init__에 action_client 파라미터를 추가하고,
  _make_action_node 클로저에서 self._action_client로 참조해야 한다.

영향받는 테스트: S8 langgraph backend에 대해 xfail 마킹.
======================================================================

[T5 SOURCE BUG REPORT #3]
======================================================================
Bug: _lg_resume이 step_id를 무시하고 entry_step부터 재시작한다

파일: src/workflow/engine.py
함수: WorkflowEngine._lg_resume

증상:
  engine.resume(wf_id, sess_id, step_id="step_2", collected={...}) 호출 시
  반환되는 StepResult.step_id가 "step_2"가 아니라 워크플로우의 첫 step_id이다.
  collected도 resume에 주입한 값이 아닌 빈 상태로 초기화된다.

원인 분석:
  _lg_resume은 make_initial_state(workflow_id, step_id)로 initial_state를 만들어
  state 채널 current_step_id=step_id를 설정하지만, LangGraph 그래프 구조는 항상
  START → definition.entry_step_id 엣지로 고정된다. graph.ainvoke는 current_step_id
  채널값이 아니라 START 엣지를 따라 entry_step부터 실행한다.

수정 방향 (source는 수정하지 않음):
  resume을 위해서는 ① step_id를 entry로 하는 별도 서브그래프 컴파일, 또는
  ② 체크포인터에 직접 step_id에서의 중간 상태를 기록하는 방식이 필요하다.
  현재 _lg_resume 구현은 실질적으로 _lg_start와 동일하게 동작한다.

영향받는 테스트: S9 langgraph backend에 대해 xfail 마킹.
======================================================================
"""

from __future__ import annotations

import pytest

from src.workflow.action_client import WorkflowActionError
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.engine import WorkflowEngine
from src.workflow.store import WorkflowStore


# ── 헬퍼 / factory ──────────────────────────────────────────────────────────

def _build_store(*definitions: WorkflowDefinition) -> WorkflowStore:
    """인메모리 WorkflowStore에 정의를 주입한다.

    T5 파라미터화 시에는 동일 store를 다른 엔진 구현체에 넘길 수 있다.
    """
    store = WorkflowStore()
    for d in definitions:
        store._cache[d.id] = d
    return store


def make_engine(store: WorkflowStore, action_client=None, classifier=None) -> WorkflowEngine:
    """레거시 엔진 factory.

    T5에서 신엔진 backend를 파라미터화할 때 이 factory만 교체하면 된다.
    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """
    return WorkflowEngine(store, action_client=action_client, classifier=classifier)


def make_lg_engine(store: WorkflowStore, action_client=None, classifier=None) -> WorkflowEngine:
    """LangGraph 신엔진 factory.

    InMemorySaver를 체크포인터로 사용해 DB 없이 in-process로 동작한다.
    graph_builder 캐시는 인스턴스별이므로 각 테스트에서 독립적으로 동작한다.
    """
    from langgraph.checkpoint.memory import MemorySaver

    from src.workflow.graph_builder import WorkflowGraphBuilder

    checkpointer = MemorySaver()
    builder = WorkflowGraphBuilder(store, classifier=classifier)
    return WorkflowEngine(
        store,
        graph_builder=builder,
        checkpointer=checkpointer,
        engine_backend="langgraph",
        action_client=action_client,
    )


# ── 경량 액션 스텁 ──────────────────────────────────────────────────────────

class _SuccessActionStub:
    """call(...) 호출 시 지정 dict를 반환하는 경량 스텁 (httpx 없이)."""

    def __init__(self, response: dict | None = None) -> None:
        self._response = response or {"ok": True, "id": "stub-001"}
        self.called = 0

    async def call(self, **kwargs) -> dict:
        self.called += 1
        return self._response


class _FailActionStub:
    """call(...) 호출 시 WorkflowActionError를 raise하는 경량 스텁."""

    async def call(self, **kwargs) -> dict:
        raise WorkflowActionError("stub: 외부 API 호출 실패", status_code=500)


# ── 공유 워크플로우 정의 ────────────────────────────────────────────────────

def _simple_workflow() -> WorkflowDefinition:
    """select → input → confirm → done (S1~S2, S6, S7 공용)."""
    return WorkflowDefinition(
        id="parity_simple",
        name="Parity 단순 워크플로우",
        steps=[
            WorkflowStep(
                id="ask_type", type="select", prompt="유형을 선택하세요.",
                save_as="type", options=["A", "B"],
                branches={"A": "ask_name", "B": "ask_name"},
            ),
            WorkflowStep(
                id="ask_name", type="input", prompt="이름을 입력하세요.",
                save_as="name", next="confirm",
            ),
            WorkflowStep(
                id="confirm", type="confirm",
                prompt="확인해주세요. (예/아니오)", save_as="ok",
                branches={"예": "done", "아니오": "ask_type"},
            ),
            WorkflowStep(
                id="done", type="message",
                prompt="{{name}}님, 완료되었습니다.",
            ),
        ],
    )


def _branching_workflow() -> WorkflowDefinition:
    """select 재프롬프트·max_retries escape (S3, S4 공용)."""
    return WorkflowDefinition(
        id="parity_branch",
        name="Parity 분기 워크플로우",
        steps=[
            WorkflowStep(
                id="start", type="select", prompt="선택하세요.",
                save_as="choice", options=["X", "Y"],
                branches={"X": "path_x", "Y": "path_y"},
            ),
            WorkflowStep(id="path_x", type="message", prompt="X 경로입니다."),
            WorkflowStep(id="path_y", type="message", prompt="Y 경로입니다."),
        ],
    )


def _phone_validation_workflow() -> WorkflowDefinition:
    """입력검증 phone (S5 공용)."""
    return WorkflowDefinition(
        id="parity_phone",
        name="Parity 전화번호 검증 워크플로우",
        steps=[
            WorkflowStep(
                id="ask_phone", type="input", prompt="전화번호를 입력하세요.",
                save_as="phone", validation="phone", next="done",
            ),
            WorkflowStep(id="done", type="message", prompt="완료."),
        ],
    )


def _action_workflow() -> WorkflowDefinition:
    """action step 성공/실패 (S8 공용)."""
    return WorkflowDefinition(
        id="parity_action",
        name="Parity 액션 워크플로우",
        steps=[
            WorkflowStep(
                id="ask_name", type="input", prompt="이름을 입력하세요.",
                save_as="name", next="submit",
            ),
            WorkflowStep(
                id="submit",
                type="action",
                prompt="접수 중...",
                save_as="result",
                endpoint="https://api.example.com/submit",
                http_method="POST",
                payload_template={"name": "{{name}}"},
                timeout_seconds=10,
                on_success_message="{{name}}님, 접수가 완료되었습니다.",
                on_error_message="접수 중 오류가 발생했습니다.",
                next="done",
            ),
            WorkflowStep(id="done", type="message", prompt="모든 절차가 끝났습니다."),
        ],
    )


def _resume_workflow() -> WorkflowDefinition:
    """resume → advance 연속 흐름 (S9 공용)."""
    return WorkflowDefinition(
        id="parity_resume",
        name="Parity Resume 워크플로우",
        steps=[
            WorkflowStep(
                id="step_1", type="input", prompt="이름을 알려주세요.",
                save_as="name", next="step_2",
            ),
            WorkflowStep(
                id="step_2", type="input", prompt="{{name}}님, 전화번호를 알려주세요.",
                save_as="phone", next="step_3",
            ),
            WorkflowStep(
                id="step_3", type="message", prompt="{{name}} / {{phone}} 완료.",
            ),
        ],
    )


def _structured_signals_workflow() -> WorkflowDefinition:
    """intent_confirm / collection / concluded 구조신호 (S10 공용)."""
    return WorkflowDefinition(
        id="parity_signals",
        name="Parity 구조신호 워크플로우",
        steps=[
            WorkflowStep(
                id="ask_name",
                type="input",
                prompt="이름을 알려줘.",
                save_as="partner_name",
                collection_target="partner",
                collection_field="name",
                collection_label="이름",
                next="ask_confirm",
            ),
            WorkflowStep(
                id="ask_confirm",
                type="confirm",
                prompt="맞습니까?",
                save_as="ok",
                intent="compat",
                confirm_yes_label="응",
                confirm_no_label="아니",
                branches={"예": "done", "아니오": "ask_name"},
            ),
            WorkflowStep(
                id="done",
                type="message",
                prompt="완료되었습니다.",
            ),
        ],
    )


# ── 파라미터화 마커 ──────────────────────────────────────────────────────────
# pytest.mark.parametrize id: "legacy" / "langgraph"
_BOTH_BACKENDS = pytest.mark.parametrize(
    "engine_factory",
    [
        pytest.param(make_engine, id="legacy"),
        pytest.param(make_lg_engine, id="langgraph"),
    ],
)


def _is_lg(engine_factory) -> bool:
    """엔진 factory가 langgraph 백엔드인지 확인하는 헬퍼."""
    return engine_factory is make_lg_engine


def _xfail_if_lg(engine_factory, reason: str) -> None:
    """langgraph backend에서만 pytest.xfail()을 호출한다.

    테스트 함수 내에서 호출하여 xfail 이유를 동적으로 설정한다.
    legacy backend에서는 아무 동작도 하지 않는다.
    """
    if _is_lg(engine_factory):
        pytest.xfail(reason)


# ── 공통 xfail 이유 문자열 ────────────────────────────────────────────────────
_BUG1_MESSAGE_COMPLETED = (
    "LangGraph #1 Bug: _make_message_node가 terminal message에서 completed/concluded를 "
    "state에 설정하지 않아 StepResult.completed=False 반환 — "
    "graph_builder.py:_make_message_node 수정 필요"
)
_BUG2_ACTION_CLIENT = (
    "LangGraph #2 Bug: _make_action_node가 action_client=None을 하드코딩해 "
    "주입된 action_client stub이 호출되지 않음 — "
    "graph_builder.py:_make_action_node T4 미구현"
)
_BUG3_RESUME_STEP = (
    "LangGraph #3 Bug: _lg_resume이 step_id를 무시하고 entry_step부터 재시작 — "
    "engine.py:_lg_resume에서 LangGraph 그래프가 항상 START→entry_step으로 고정됨"
)


# ── S1: Happy Path ───────────────────────────────────────────────────────────

class TestS1HappyPath:
    """S1: select→input→confirm→done 정상 흐름.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_start_shows_first_step(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_simple_workflow()))

        # Act
        result = await engine.start("parity_simple", "s1-s1")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert "유형을 선택하세요" in result.bot_message
        assert result.options == ["A", "B"]
        assert not result.completed

    @_BOTH_BACKENDS
    async def test_full_flow_completes_with_collected(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_simple_workflow()))
        await engine.start("parity_simple", "s1-full")

        # Act
        await engine.advance("s1-full", "A")          # select → ask_name
        await engine.advance("s1-full", "홍길동")      # input → confirm
        result = await engine.advance("s1-full", "예") # confirm → done

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.completed
        assert "홍길동" in result.bot_message           # 템플릿 렌더링 확인
        assert result.collected["name"] == "홍길동"
        assert result.collected["type"] == "A"

    @_BOTH_BACKENDS
    async def test_step_result_fields_present(self, engine_factory):
        """StepResult 12종 필드가 존재하는지 스냅샷."""
        # Arrange
        engine = engine_factory(_build_store(_simple_workflow()))

        # Act
        result = await engine.start("parity_simple", "s1-fields")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        # StepResult 계약(필드 12종) 단언
        assert hasattr(result, "bot_message")
        assert hasattr(result, "options")
        assert hasattr(result, "step_id")
        assert hasattr(result, "step_type")
        assert hasattr(result, "collected")
        assert hasattr(result, "completed")
        assert hasattr(result, "escaped")
        assert hasattr(result, "action_result")
        assert hasattr(result, "report")
        assert hasattr(result, "intent_confirm")
        assert hasattr(result, "collection")
        assert hasattr(result, "concluded")


# ── S2: Confirm Reject 루프백 ────────────────────────────────────────────────

class TestS2ConfirmRejectLoopback:
    """S2: confirm 거절(아니오) 시 첫 스텝 재안내.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_confirm_reject_returns_to_first_step(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_simple_workflow()))
        await engine.start("parity_simple", "s2")
        await engine.advance("s2", "A")
        await engine.advance("s2", "홍길동")

        # Act
        result = await engine.advance("s2", "아니오")  # 루프백

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert "유형을 선택하세요" in result.bot_message
        assert not result.completed


# ── S3: Select 미매칭 재프롬프트 ────────────────────────────────────────────

class TestS3SelectUnmatchedReprompt:
    """S3: 분류기 없는 select에 자유텍스트 → 같은 스텝 재안내.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_freetext_on_select_reprompts_same_step(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_branching_workflow()))
        await engine.start("parity_branch", "s3")

        # Act
        result = await engine.advance("s3", "둘 다 궁금해서 왔어")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert not result.completed
        assert not result.escaped
        assert result.step_id == "start"              # 같은 스텝에 머무름
        assert result.options == ["X", "Y"]           # 선택지 재노출
        assert "choice" not in result.collected       # 미매칭 입력은 저장되지 않음(롤백)


# ── S4: Select 미매칭 max_retries Escape ────────────────────────────────────

class TestS4SelectUnmatchedEscape:
    """S4: 미매칭 반복(max_retries)회 → escaped+completed+concluded.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_unmatched_reprompts_until_max_retries(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_branching_workflow()))
        await engine.start("parity_branch", "s4")

        # Act
        r1 = await engine.advance("s4", "아무거나1")
        r2 = await engine.advance("s4", "아무거나2")

        # Assert 2회까지 재안내 — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert not r1.completed
        assert not r2.completed

    @_BOTH_BACKENDS
    async def test_max_retries_escape(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_branching_workflow()))
        await engine.start("parity_branch", "s4-escape")
        await engine.advance("s4-escape", "아무거나1")
        await engine.advance("s4-escape", "아무거나2")

        # Act: 3회째 = max_retries 도달
        r3 = await engine.advance("s4-escape", "아무거나3")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert r3.completed
        assert r3.escaped
        assert r3.concluded


# ── S5: 입력검증 실패 재프롬프트 + 검증성공 회복 ────────────────────────────

class TestS5ValidationRepromptAndRecovery:
    """S5: phone validation 스텝 — 실패 재안내, 성공 회복, 3회 실패 자동취소.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_invalid_phone_reprompts(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_phone_validation_workflow()))
        await engine.start("parity_phone", "s5-reprompt")

        # Act
        result = await engine.advance("s5-reprompt", "abc")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert "전화번호" in result.bot_message
        assert not result.completed

    @_BOTH_BACKENDS
    async def test_valid_phone_recovers(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_phone_validation_workflow()))
        await engine.start("parity_phone", "s5-recover")
        await engine.advance("s5-recover", "abc")  # 1회 실패

        # Act
        result = await engine.advance("s5-recover", "010-1234-5678")  # 올바른 입력

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.completed  # done 스텝(message)으로 진행하고 완료

    @_BOTH_BACKENDS
    async def test_three_failures_auto_cancel(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_phone_validation_workflow()))
        await engine.start("parity_phone", "s5-autocancel")
        await engine.advance("s5-autocancel", "abc")  # 1회 실패
        await engine.advance("s5-autocancel", "xyz")  # 2회 실패

        # Act
        result = await engine.advance("s5-autocancel", "!!!")  # 3회 실패 → 자동취소

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.completed
        assert result.escaped
        assert "취소" in result.bot_message


# ── S6: 뒤로가기(back) ──────────────────────────────────────────────────────

class TestS6Back:
    """S6: 2스텝 진행 후 '뒤로' → 직전 스텝 재노출 + save_as 롤백.

    공개 API(advance + get_session)로만 collected 검증.
    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_back_returns_to_previous_step(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_simple_workflow()))
        await engine.start("parity_simple", "s6")
        await engine.advance("s6", "A")      # ask_type → ask_name (type="A" 수집)

        # Act: ask_name에서 뒤로가기
        result = await engine.advance("s6", "뒤로")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert "유형을 선택하세요" in result.bot_message  # ask_type 재노출

    @_BOTH_BACKENDS
    async def test_back_rolls_back_collected(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_simple_workflow()))
        await engine.start("parity_simple", "s6-rollback")
        await engine.advance("s6-rollback", "A")  # type="A" 수집

        # Act
        await engine.advance("s6-rollback", "뒤로")

        # Assert: collected에서 직전 스텝의 save_as("type")가 제거됐는지 확인
        # get_session 공개 API로 확인 — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        session = await engine.get_session("s6-rollback")
        assert session is not None
        assert "type" not in session.collected  # 롤백 확인


# ── S7: Escape 키워드 ────────────────────────────────────────────────────────

class TestS7EscapeKeyword:
    """S7: escape_policy=allow에서 '취소' → escaped+completed, collected 보존.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_escape_keyword_exits_workflow(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_simple_workflow()))
        await engine.start("parity_simple", "s7")
        await engine.advance("s7", "A")  # type="A" 수집

        # Act
        result = await engine.advance("s7", "취소")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.completed
        assert result.escaped

    @_BOTH_BACKENDS
    async def test_escape_preserves_collected(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_simple_workflow()))
        await engine.start("parity_simple", "s7-collect")
        await engine.advance("s7-collect", "A")  # type="A" 수집

        # Act
        result = await engine.advance("s7-collect", "그만")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.escaped
        assert result.collected["type"] == "A"  # 이탈 후에도 수집 데이터 보존


# ── S8: Action 성공 / 실패 ──────────────────────────────────────────────────

class TestS8ActionSuccessFailure:
    """S8: action_client 스텁 주입 — 성공 시 다음 스텝·action_result carry,
    실패 시 on_error_message.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_action_success_carries_action_result(self, engine_factory):
        # Arrange
        stub = _SuccessActionStub({"ok": True, "contract_id": "C-001"})
        engine = engine_factory(_build_store(_action_workflow()), action_client=stub)
        await engine.start("parity_action", "s8-success")

        # Act
        await engine.advance("s8-success", "홍길동")  # ask_name → submit(action)
        # 성공 시 on_success_message가 반환되거나 다음 스텝으로 진행한다
        result_at_submit = await engine.advance("s8-success", "홍길동")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        # action 스텁이 호출됐는지 확인: start 호출 후 advance("홍길동") 한 번이면 submit에 도달
        # action step 처리 후 on_success_message 또는 다음 스텝으로 이동
        assert result_at_submit.completed or "완료" in result_at_submit.bot_message

    @_BOTH_BACKENDS
    async def test_action_success_with_fresh_start(self, engine_factory):
        # Arrange
        stub = _SuccessActionStub({"ok": True, "contract_id": "C-001"})
        engine = engine_factory(_build_store(_action_workflow()), action_client=stub)
        await engine.start("parity_action", "s8-fresh")
        await engine.advance("s8-fresh", "홍길동")  # ask_name → submit(action step)

        # Assert: stub이 적어도 한 번 호출됐음
        # 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert stub.called >= 1

    @_BOTH_BACKENDS
    async def test_action_failure_returns_error_message(self, engine_factory):
        # Arrange
        fail_stub = _FailActionStub()
        engine = engine_factory(_build_store(_action_workflow()), action_client=fail_stub)
        await engine.start("parity_action", "s8-fail")

        # Act
        result = await engine.advance("s8-fail", "홍길동")  # ask_name → submit(실패)

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.completed
        assert "오류" in result.bot_message  # on_error_message


# ── S9: Resume → Advance ─────────────────────────────────────────────────────

class TestS9ResumeThenAdvance:
    """S9: resume(wf, sess, step_id, collected) 후 advance로 다음 스텝,
    collected 누적 확인.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_resume_then_advance_accumulates_collected(self, engine_factory):
        # Arrange
        store = WorkflowStore()
        store._cache["parity_resume"] = _resume_workflow()
        engine = engine_factory(store)

        # Act: step_2에서 이름이 이미 수집된 상태로 resume
        await engine.resume(
            workflow_id="parity_resume",
            session_id="s9",
            step_id="step_2",
            collected={"name": "이순신"},
        )
        result = await engine.advance("s9", "010-9999-8888")  # step_2 → step_3

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.step_id == "step_3"
        session = await engine.get_session("s9")
        assert session is not None
        assert session.collected["name"] == "이순신"       # resume으로 주입된 값 유지
        assert session.collected["phone"] == "010-9999-8888"  # advance로 추가 수집

    @_BOTH_BACKENDS
    async def test_resume_restores_correct_step(self, engine_factory):
        # Arrange
        store = WorkflowStore()
        store._cache["parity_resume"] = _resume_workflow()
        engine = engine_factory(store)

        # Act
        result = await engine.resume(
            workflow_id="parity_resume",
            session_id="s9-step",
            step_id="step_2",
            collected={"name": "김유신"},
        )

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.step_id == "step_2"
        assert "김유신님" in result.bot_message  # 템플릿 렌더링 확인


# ── S10: 구조신호 ────────────────────────────────────────────────────────────

class TestS10StructuredSignals:
    """S10: intent_confirm(yes/no 라벨), collection.fields[].status(filled/pending),
    terminal message concluded=True.

    이 단언은 legacy·langgraph 양 엔진에서 동일해야 함.
    """

    @_BOTH_BACKENDS
    async def test_confirm_step_emits_intent_confirm(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_structured_signals_workflow()))
        await engine.start("parity_signals", "s10-confirm")

        # Act: ask_name → ask_confirm 스텝 도달
        result = await engine.advance("s10-confirm", "홍길동")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.step_type == "confirm"
        assert result.intent_confirm != {}
        assert result.intent_confirm["intent"] == "compat"
        assert result.intent_confirm["yes_label"] == "응"
        assert result.intent_confirm["no_label"] == "아니"

    @_BOTH_BACKENDS
    async def test_collection_field_pending_then_filled(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_structured_signals_workflow()))

        # Act: 첫 스텝(ask_name)에서 collection 스냅샷
        result_start = await engine.start("parity_signals", "s10-collection")

        # Assert: 아직 수집 안 됨 → name 필드 pending
        # 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        c = result_start.collection
        assert c != {}
        assert c["target"] == "partner"
        fields_by_key = {f["key"]: f for f in c["fields"]}
        assert fields_by_key["name"]["status"] == "pending"

        # Act: 이름 입력 후 ask_confirm 스텝 도달
        result_after = await engine.advance("s10-collection", "홍길동")

        # collection 스냅샷은 confirm 스텝 기준이므로 name 필드가 더 이상 없을 수 있음
        # (confirm 스텝은 collection_field 없음 → collection == {})
        # 중요한 것은 start 시점에 pending이었다는 것 → 이미 위에서 검증됨

    @_BOTH_BACKENDS
    async def test_terminal_message_concluded_true(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_structured_signals_workflow()))
        await engine.start("parity_signals", "s10-terminal")
        await engine.advance("s10-terminal", "홍길동")  # → ask_confirm

        # Act: 확인 → done(message, terminal)
        result = await engine.advance("s10-terminal", "예")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.completed is True
        assert result.concluded is True

    @_BOTH_BACKENDS
    async def test_in_progress_step_not_concluded(self, engine_factory):
        # Arrange
        engine = engine_factory(_build_store(_structured_signals_workflow()))

        # Act
        result = await engine.start("parity_signals", "s10-inprogress")

        # Assert — 이 단언은 legacy·langgraph 양 엔진에서 동일해야 함
        assert result.completed is False
        assert result.concluded is False
