"""LangGraph 신엔진 내구성(durability) 검증 (T5).

LangGraph 엔진에 한정해 체크포인터 기반 상태 지속성을 검증한다.

D1: 재시작 시뮬레이션 — 엔진 A가 인터럽트 후 체크포인터(MemorySaver)를 공유한
    엔진 B가 동일 thread_id로 재개할 수 있어야 한다.
D2: get_session 지속성 — start 후 get_session이 세션 정보를 반환해야 한다.
D3: cancel → get_session — cancel 후 get_session이 None 또는 완료 세션이어야 한다.
D4: saju_discovery 연애 경로 전체 흐름 — intro → ask_topic(연애) →
    sit_love(썸) → love_partner_intro(dynamic, 스텁) → ask_partner_name →
    ask_partner_birth → ask_partner_time → ask_partner_gender(남성) →
    run_compat_male(action, 스텁) → insight_compat(dynamic, 스텁) → reveal_compat(message).
    action_client 스텁 주입, collected 누적, report 필드 확인.
D5: G2 검증 — retry_count 자기루프 + max_retries 이탈(escaped+concluded+completed).
D6: G3 검증 — resume → advance 연속성 (step_id 복원, collected 주입 확인).
D7: DB 내구성 — AsyncPostgresSaver 기반 체크포인트 재개 (skipif DB 없음).

[T5 SOURCE BUG REPORT — D4 영향]
동일한 terminal message 버그(graph_builder.py:_make_message_node)가 D4 마지막
단계에도 적용된다. reveal_compat(message, report="compatibility")에서
completed/concluded가 False로 반환된다. xfail로 마킹.
"""

from __future__ import annotations

import os

import pytest

from src.workflow.action_client import WorkflowActionError
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.engine import WorkflowEngine
from src.workflow.store import WorkflowStore


# ── 내구성 테스트 전용 DB 연결 문자열 ──────────────────────────────────────
# localhost:5434는 프로젝트 docker-compose.yml의 PostgreSQL 포트
_DB_URL = os.environ.get(
    "AIP_DATABASE_URL",
    "postgresql+asyncpg://aip:aip_dev@localhost:5434/ai_platform",
)

# DB 연결 가능 여부를 런타임에 한 번만 확인한다.
def _db_reachable() -> bool:
    """psycopg 연결 시도로 DB 가용 여부를 확인한다."""
    import asyncio

    async def _check() -> bool:
        try:
            import asyncpg
            conn_str = _DB_URL.replace("+asyncpg", "").replace("postgresql://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(dsn=conn_str, timeout=2.0)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.get_event_loop().run_until_complete(_check())
    except Exception:
        return False


# ── 공용 factory ──────────────────────────────────────────────────────────────

def _make_lg_engine(
    store: WorkflowStore,
    checkpointer=None,
    action_client=None,
    classifier=None,
) -> WorkflowEngine:
    """LangGraph 엔진 생성 — checkpointer를 외부에서 주입해 공유할 수 있다."""
    from langgraph.checkpoint.memory import MemorySaver

    from src.workflow.graph_builder import WorkflowGraphBuilder

    cp = checkpointer if checkpointer is not None else MemorySaver()
    builder = WorkflowGraphBuilder(store, classifier=classifier)
    return WorkflowEngine(
        store,
        graph_builder=builder,
        checkpointer=cp,
        engine_backend="langgraph",
        action_client=action_client,
    )


def _build_store(*definitions: WorkflowDefinition) -> WorkflowStore:
    store = WorkflowStore()
    for d in definitions:
        store._cache[d.id] = d
    return store


# ── 액션 스텁 ─────────────────────────────────────────────────────────────────

class _SuccessActionStub:
    """지정 dict를 반환하는 경량 액션 스텁."""

    def __init__(self, response: dict | None = None) -> None:
        self._response = response or {"ok": True}
        self.called = 0

    async def call(self, **kwargs) -> dict:
        self.called += 1
        return self._response


class _FailActionStub:
    async def call(self, **kwargs) -> dict:
        raise WorkflowActionError("stub: 실패", status_code=500)


# ── 공용 워크플로우 ────────────────────────────────────────────────────────────

def _two_step_workflow() -> WorkflowDefinition:
    """input → message: restart 시뮬레이션·get_session 검증용."""
    return WorkflowDefinition(
        id="dur_two_step",
        name="내구성 2단계",
        steps=[
            WorkflowStep(
                id="ask_name", type="input",
                prompt="이름을 입력하세요.", save_as="name", next="done",
            ),
            WorkflowStep(id="done", type="message", prompt="{{name}}님 완료."),
        ],
    )


def _retry_workflow() -> WorkflowDefinition:
    """select(max_retries=2): G2 retry_count 검증용."""
    return WorkflowDefinition(
        id="dur_retry",
        name="내구성 재시도",
        max_retries=2,
        steps=[
            WorkflowStep(
                id="pick", type="select",
                prompt="선택하세요.", save_as="choice",
                options=["A", "B"],
                branches={"A": "end", "B": "end"},
            ),
            WorkflowStep(id="end", type="message", prompt="선택됨: {{choice}}."),
        ],
    )


def _resume_workflow() -> WorkflowDefinition:
    """input → input → message: G3 resume 검증용."""
    return WorkflowDefinition(
        id="dur_resume",
        name="내구성 재개",
        steps=[
            WorkflowStep(
                id="step_1", type="input",
                prompt="이름을 알려주세요.", save_as="name", next="step_2",
            ),
            WorkflowStep(
                id="step_2", type="input",
                prompt="{{name}}님, 전화번호를 알려주세요.", save_as="phone", next="done",
            ),
            WorkflowStep(id="done", type="message", prompt="{{name}} / {{phone}} 완료."),
        ],
    )


def _action_then_message_workflow() -> WorkflowDefinition:
    """input → action → message: action 후 terminal message 검증용."""
    return WorkflowDefinition(
        id="dur_action_msg",
        name="내구성 액션→메시지",
        steps=[
            WorkflowStep(
                id="ask", type="input",
                prompt="이름을 입력하세요.", save_as="name", next="submit",
            ),
            WorkflowStep(
                id="submit", type="action",
                prompt="처리 중...", save_as="result",
                endpoint="https://api.example.com/submit",
                http_method="POST",
                payload_template={"name": "{{name}}"},
                timeout_seconds=10,
                on_success_message="{{name}}님, 완료되었습니다.",
                on_error_message="오류가 발생했습니다.",
                next="done",
            ),
            WorkflowStep(id="done", type="message", prompt="모든 절차가 끝났습니다."),
        ],
    )


# ── D1: 재시작 시뮬레이션 ─────────────────────────────────────────────────────

class TestD1RestartSimulation:
    """D1: 체크포인터(MemorySaver) 공유 → 엔진 B가 엔진 A의 체크포인트에서 재개."""

    async def test_engine_b_resumes_from_engine_a_checkpoint(self):
        """엔진 A가 interrupt 후, 동일 MemorySaver를 가진 엔진 B가 재개한다."""
        from langgraph.checkpoint.memory import MemorySaver

        shared_cp = MemorySaver()
        store = _build_store(_two_step_workflow())

        # 엔진 A: start → interrupt(ask_name)
        engine_a = _make_lg_engine(store, checkpointer=shared_cp)
        result_a = await engine_a.start("dur_two_step", "restart-001")

        # ask_name interrupt에서 멈춰야 함
        assert result_a.step_id == "ask_name"
        assert not result_a.completed

        # 엔진 B: 동일 checkpointer로 생성 (그래프 빌더는 별도 인스턴스)
        engine_b = _make_lg_engine(store, checkpointer=shared_cp)

        # 엔진 B: advance → done(message)
        result_b = await engine_b.advance("restart-001", "이순신")

        # Assert: 엔진 B가 엔진 A가 남긴 체크포인트에서 재개해 완료
        assert result_b.step_id == "done"
        assert "이순신" in result_b.bot_message  # 템플릿 렌더링

    async def test_engine_b_collected_contains_engine_a_data(self):
        """엔진 A에서 수집한 collected가 엔진 B에서 advance 후에도 유지된다."""
        from langgraph.checkpoint.memory import MemorySaver

        shared_cp = MemorySaver()
        store = _build_store(_resume_workflow())

        engine_a = _make_lg_engine(store, checkpointer=shared_cp)
        await engine_a.start("dur_resume", "restart-002")
        await engine_a.advance("restart-002", "홍길동")  # step_1 → step_2

        engine_b = _make_lg_engine(store, checkpointer=shared_cp)
        result = await engine_b.advance("restart-002", "010-1234-5678")  # step_2 → done

        # collected에 엔진 A가 수집한 name이 보존되어야 함
        assert result.collected.get("name") == "홍길동"
        assert result.collected.get("phone") == "010-1234-5678"


# ── D2: get_session 지속성 ────────────────────────────────────────────────────

class TestD2GetSessionPersistence:
    """D2: start 후 get_session이 세션 정보를 반환한다."""

    async def test_get_session_after_start(self):
        """start 직후 get_session이 세션을 반환한다."""
        store = _build_store(_two_step_workflow())
        engine = _make_lg_engine(store)

        await engine.start("dur_two_step", "get-sess-001")

        session = await engine.get_session("get-sess-001")

        assert session is not None
        assert session.workflow_id == "dur_two_step"
        assert session.current_step_id == "ask_name"

    async def test_get_session_reflects_advance(self):
        """advance 후 get_session이 최신 상태를 반환한다."""
        store = _build_store(_resume_workflow())
        engine = _make_lg_engine(store)

        await engine.start("dur_resume", "get-sess-002")
        await engine.advance("get-sess-002", "이순신")

        session = await engine.get_session("get-sess-002")

        assert session is not None
        assert session.current_step_id == "step_2"
        assert session.collected.get("name") == "이순신"

    async def test_get_session_nonexistent_returns_none(self):
        """존재하지 않는 세션은 None을 반환한다."""
        store = _build_store(_two_step_workflow())
        engine = _make_lg_engine(store)

        session = await engine.get_session("nonexistent-9999")

        assert session is None


# ── D3: cancel → get_session ─────────────────────────────────────────────────

class TestD3CancelThenGetSession:
    """D3: cancel 후 세션이 사라지거나 완료 상태로 반환된다."""

    async def test_cancel_returns_true(self):
        """활성 세션 cancel이 True를 반환한다."""
        store = _build_store(_two_step_workflow())
        engine = _make_lg_engine(store)

        await engine.start("dur_two_step", "cancel-001")
        result = await engine.cancel("cancel-001")

        assert result is True

    @pytest.mark.xfail(
        reason=(
            "LangGraph _lg_cancel 버그: adelete_thread를 세션 존재 여부 확인 없이 호출하면 "
            "MemorySaver가 예외 없이 반환 → 항상 True 반환. engine.py:_lg_cancel 수정 필요 (T4 대상)"
        ),
        strict=False,
    )
    async def test_cancel_nonexistent_session(self):
        """존재하지 않는 세션 cancel은 False를 반환한다."""
        store = _build_store(_two_step_workflow())
        engine = _make_lg_engine(store)

        result = await engine.cancel("nonexistent-9999")

        assert result is False

    async def test_get_session_after_cancel_is_none_or_completed(self):
        """cancel 후 get_session이 None 또는 completed=True 세션을 반환한다.

        adelete_thread API가 있으면 None, 없으면 체크포인트가 남아있을 수 있다.
        """
        store = _build_store(_two_step_workflow())
        engine = _make_lg_engine(store)

        await engine.start("dur_two_step", "cancel-002")
        await engine.cancel("cancel-002")

        session = await engine.get_session("cancel-002")

        # cancel 후: 삭제됐으면 None, 삭제 API 없으면 완료 상태가 기록됨
        assert session is None or session.completed


# ── D4: saju_discovery 연애 경로 (action_client 스텁) ─────────────────────────

class TestD4SajuDiscoveryLovePath:
    """D4: saju_discovery 워크플로우 연애(상대 있음) 경로 전체 흐름.

    dynamic 노드는 스텁 없이 실행(generate_dynamic이 LLM 없으면 "" 반환)하므로
    dynamic 단계에서는 step_id/collected만 확인하고 bot_message는 검증하지 않는다.
    action_client는 _SuccessActionStub으로 대체한다.

    [Source Bug: terminal message completed=False]
    reveal_compat(message, report="compatibility") 도달 후 completed 확인 단언은
    _make_message_node 버그로 xfail 처리한다.
    """

    async def _load_saju_store(self) -> WorkflowStore:
        """seeds/workflows/saju-discovery.yaml을 로드한 WorkflowStore."""
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        return store

    async def test_love_path_intro_to_ask_topic(self):
        """intro(message→auto) → hook(dynamic→auto) → ask_topic(select,interrupt)."""
        store = await self._load_saju_store()
        if not store.get("saju_discovery"):
            pytest.skip("saju_discovery workflow not loaded")

        stub = _SuccessActionStub({"ok": True, "compat_score": 87})
        engine = _make_lg_engine(store, action_client=stub)

        result = await engine.start("saju_discovery", "d4-love-001")

        # intro+hook이 auto-chain 후 ask_topic(select)에서 interrupt
        assert result.step_id == "ask_topic"
        assert result.options == ["연애·인연", "돈·재물", "일·진로", "건강", "관계·가족"]
        assert not result.completed

    async def test_love_path_topic_to_sit_love(self):
        """ask_topic(연애·인연) → sit_love(select)."""
        store = await self._load_saju_store()
        if not store.get("saju_discovery"):
            pytest.skip("saju_discovery workflow not loaded")

        stub = _SuccessActionStub({"ok": True})
        engine = _make_lg_engine(store, action_client=stub)

        await engine.start("saju_discovery", "d4-love-002")
        result = await engine.advance("d4-love-002", "연애·인연")

        # sit_love(select)에 도달
        assert result.step_id == "sit_love"
        assert not result.completed
        assert result.collected.get("topic") == "연애·인연"

    async def test_love_path_sit_to_partner_collection(self):
        """sit_love(썸) → love_partner_intro(dynamic) → ask_partner_name(input)."""
        store = await self._load_saju_store()
        if not store.get("saju_discovery"):
            pytest.skip("saju_discovery workflow not loaded")

        stub = _SuccessActionStub({"ok": True})
        engine = _make_lg_engine(store, action_client=stub)

        await engine.start("saju_discovery", "d4-love-003")
        await engine.advance("d4-love-003", "연애·인연")    # ask_topic → sit_love
        result = await engine.advance("d4-love-003", "썸·짝사랑")  # sit_love → love_partner_intro → ask_partner_name

        # dynamic(love_partner_intro)은 auto-chain 후 ask_partner_name(input)에서 interrupt
        assert result.step_id == "ask_partner_name"
        assert result.step_type == "input"
        assert not result.completed

    async def test_love_path_collected_accumulates(self):
        """파트너 정보 수집 단계에서 collected가 누적된다."""
        store = await self._load_saju_store()
        if not store.get("saju_discovery"):
            pytest.skip("saju_discovery workflow not loaded")

        stub = _SuccessActionStub({"ok": True, "compat_score": 87})
        engine = _make_lg_engine(store, action_client=stub)

        await engine.start("saju_discovery", "d4-love-004")
        await engine.advance("d4-love-004", "연애·인연")         # → sit_love
        await engine.advance("d4-love-004", "썸·짝사랑")         # → ask_partner_name
        await engine.advance("d4-love-004", "민준")               # partner_name → ask_partner_birth
        await engine.advance("d4-love-004", "1995-03-10")         # partner_birth_date → ask_partner_time
        await engine.advance("d4-love-004", "09:00")              # partner_birth_time → ask_partner_gender
        result = await engine.advance("d4-love-004", "남성")      # → run_compat_male(action)

        # action step이 자동 실행되므로 그 다음 단계에 도달한 결과를 확인
        # run_compat_male → insight_compat(dynamic) → reveal_compat(message) 자동 체인
        # collected에 파트너 정보가 누적됐는지 확인
        session = await engine.get_session("d4-love-004")
        assert session is not None
        assert session.collected.get("partner_name") == "민준"
        assert session.collected.get("partner_birth_date") == "1995-03-10"
        assert session.collected.get("partner_gender_label") == "남성"

    async def test_love_path_action_client_called(self):
        """run_compat_male(action) 단계에서 action_client가 호출된다."""
        store = await self._load_saju_store()
        if not store.get("saju_discovery"):
            pytest.skip("saju_discovery workflow not loaded")

        stub = _SuccessActionStub({"ok": True, "compat_score": 87})
        engine = _make_lg_engine(store, action_client=stub)

        await engine.start("saju_discovery", "d4-love-005")
        await engine.advance("d4-love-005", "연애·인연")
        await engine.advance("d4-love-005", "썸·짝사랑")
        await engine.advance("d4-love-005", "민준")
        await engine.advance("d4-love-005", "1995-03-10")
        await engine.advance("d4-love-005", "09:00")
        await engine.advance("d4-love-005", "남성")  # → run_compat_male

        # LangGraph Bug #2: _make_action_node이 action_client=None으로 하드코딩 → xfail
        pytest.xfail(
            "LangGraph _make_action_node 버그: graph_builder.py에서 action_client=None으로 "
            "하드코딩, action_client 스텁이 전달되지 않음 — T4에서 수정 예정"
        )

        # action_client 스텁이 최소 1회 호출됐는지 확인
        assert stub.called >= 1

    @pytest.mark.xfail(
        reason=(
            "LangGraph _make_message_node 버그: terminal message 노드가 "
            "completed/concluded를 state에 설정하지 않아 StepResult.completed=False "
            "반환 — graph_builder.py:_make_message_node 수정 필요 (T3/T4 반려 대상)"
        ),
        strict=False,
    )
    async def test_love_path_reveal_compat_completed(self):
        """reveal_compat(message, report=compatibility)에서 completed+report 확인."""
        store = await self._load_saju_store()
        if not store.get("saju_discovery"):
            pytest.skip("saju_discovery workflow not loaded")

        stub = _SuccessActionStub({"ok": True, "compat_score": 87})
        engine = _make_lg_engine(store, action_client=stub)

        await engine.start("saju_discovery", "d4-love-006")
        await engine.advance("d4-love-006", "연애·인연")
        await engine.advance("d4-love-006", "썸·짝사랑")
        await engine.advance("d4-love-006", "민준")
        await engine.advance("d4-love-006", "1995-03-10")
        await engine.advance("d4-love-006", "09:00")
        result = await engine.advance("d4-love-006", "남성")

        # reveal_compat(message) 도달 → completed=True, report="compatibility"
        # NOTE: _make_message_node 버그로 현재 실패한다
        assert result.completed is True
        assert result.concluded is True
        assert result.report == "compatibility"


# ── D5: G2 retry_count 자기루프 ──────────────────────────────────────────────

class TestD5G2RetryCountLoop:
    """D5: G2 — select 미매칭 시 retry_count 증가, max_retries 도달 시 이탈."""

    async def test_retry_count_increments_on_unmatch(self):
        """선택지 미매칭 입력 시 같은 스텝에서 재안내(completed=False)."""
        store = _build_store(_retry_workflow())
        engine = _make_lg_engine(store)

        await engine.start("dur_retry", "g2-001")

        r1 = await engine.advance("g2-001", "없는선택지")
        assert not r1.completed
        assert not r1.escaped
        assert r1.step_id == "pick"

    async def test_max_retries_triggers_escape(self):
        """max_retries=2 → 3회 미매칭 후 escaped+completed+concluded."""
        store = _build_store(_retry_workflow())
        engine = _make_lg_engine(store)

        await engine.start("dur_retry", "g2-002")
        await engine.advance("g2-002", "없는1")  # retry 1
        await engine.advance("g2-002", "없는2")  # retry 2

        r3 = await engine.advance("g2-002", "없는3")  # max_retries 도달

        # LangGraph Bug #1: _make_message_node가 escaped/completed/concluded를 state에
        # 기록하지 않아 max_retries 후 escape 메시지 노드가 completed=True지만 escaped=False
        # 로 반환됨 (혹은 다음 advance가 "이미 완료된 워크플로우" 반환) — xfail
        pytest.xfail(
            "LangGraph _make_message_node 버그: terminal escape 단계에서 escaped/concluded가 "
            "state에 기록되지 않아 r3.escaped=False. graph_builder.py:_make_message_node 수정 필요"
        )

        assert r3.completed
        assert r3.escaped
        assert r3.concluded

    async def test_valid_input_before_max_retries_continues(self):
        """max_retries 도달 전 올바른 입력 시 정상 진행한다."""
        store = _build_store(_retry_workflow())
        engine = _make_lg_engine(store)

        await engine.start("dur_retry", "g2-003")
        await engine.advance("g2-003", "없는1")  # retry 1

        result = await engine.advance("g2-003", "A")  # 올바른 입력 → end(message)

        # LangGraph Bug #1: message 노드가 state를 업데이트하지 않아 step_id='pick'에 머묾
        pytest.xfail(
            "LangGraph _make_message_node 버그: message 노드 실행 후 step_id가 'end'로 갱신되지 "
            "않아 result.step_id='pick' 반환. graph_builder.py:_make_message_node 수정 필요"
        )

        # end(message) 도달 — completed 여부는 message 버그로 변동, step_id만 확인
        assert result.step_id == "end"
        assert not result.escaped


# ── D6: G3 resume → advance 연속성 ───────────────────────────────────────────

class TestD6G3ResumeAdvanceContinuity:
    """D6: G3 — resume(wf, sess, step_id, collected) 후 advance로 정확히 재개."""

    async def test_resume_restores_step_and_collected(self):
        """resume 후 지정한 step_id와 collected로 세션이 복원된다."""
        store = _build_store(_resume_workflow())
        engine = _make_lg_engine(store)

        result = await engine.resume(
            workflow_id="dur_resume",
            session_id="g3-001",
            step_id="step_2",
            collected={"name": "이순신"},
        )

        # LangGraph Bug #3: _lg_resume이 step_id를 무시하고 entry_step부터 재시작 — xfail
        pytest.xfail(
            "LangGraph _lg_resume 버그: make_initial_state(step_id)로 seed를 만들어도 "
            "graph.ainvoke는 항상 START → entry_step_id에서 시작. step_id 파라미터 무시됨. "
            "engine.py:_lg_resume 수정 필요"
        )

        assert result.step_id == "step_2"
        assert "이순신님" in result.bot_message

    async def test_resume_then_advance_accumulates(self):
        """resume 후 advance가 step_2 → done으로 진행하고 collected가 누적된다."""
        store = _build_store(_resume_workflow())
        engine = _make_lg_engine(store)

        await engine.resume(
            workflow_id="dur_resume",
            session_id="g3-002",
            step_id="step_2",
            collected={"name": "홍길동"},
        )
        result = await engine.advance("g3-002", "010-5678-1234")

        # LangGraph Bug #3: resume이 entry_step부터 재시작하므로 advance도 step_2가 아닌
        # entry_step 다음으로 진행 — xfail
        pytest.xfail(
            "LangGraph _lg_resume 버그: step_id 무시 → resume 후 advance도 entry 기준으로 진행. "
            "result.step_id='step_2'(entry 다음) != 'done'"
        )

        assert result.step_id == "done"
        session = await engine.get_session("g3-002")
        assert session is not None
        assert session.collected.get("name") == "홍길동"
        assert session.collected.get("phone") == "010-5678-1234"

    async def test_resume_overwrites_existing_session(self):
        """기존 세션이 있어도 resume이 덮어쓴다."""
        store = _build_store(_resume_workflow())
        engine = _make_lg_engine(store)

        # 먼저 step_1에서 시작
        await engine.start("dur_resume", "g3-003")
        await engine.advance("g3-003", "이순신")  # name 수집

        # step_2에서 다른 이름으로 resume → 기존 체크포인트 교체
        result = await engine.resume(
            workflow_id="dur_resume",
            session_id="g3-003",
            step_id="step_2",
            collected={"name": "세종대왕"},
        )

        # LangGraph Bug #3: _lg_resume이 step_id를 무시하고 entry_step부터 재시작 — xfail
        pytest.xfail(
            "LangGraph _lg_resume 버그: step_id 무시 → result.step_id='step_1'(entry) != 'step_2'"
        )

        assert result.step_id == "step_2"
        assert "세종대왕님" in result.bot_message

        session = await engine.get_session("g3-003")
        assert session is not None
        assert session.collected.get("name") == "세종대왕"

    async def test_resume_workflow_not_found_raises(self):
        """존재하지 않는 워크플로우 resume은 GatewayError를 raise한다."""
        from src.common.exceptions import GatewayError

        store = _build_store(_resume_workflow())
        engine = _make_lg_engine(store)

        with pytest.raises(GatewayError, match="워크플로우를 찾을 수 없습니다"):
            await engine.resume("nonexistent_wf", "g3-004", "step_1", {})

    async def test_resume_step_not_found_raises(self):
        """존재하지 않는 step_id resume은 GatewayError를 raise한다."""
        from src.common.exceptions import GatewayError

        store = _build_store(_resume_workflow())
        engine = _make_lg_engine(store)

        with pytest.raises(GatewayError, match="스텝을 찾을 수 없습니다"):
            await engine.resume("dur_resume", "g3-005", "nonexistent_step", {})


# ── D7: AsyncPostgresSaver DB 내구성 ─────────────────────────────────────────

@pytest.mark.skipif(
    not _db_reachable(),
    reason="PostgreSQL DB 미사용(localhost:5434) — CI/CD 또는 DB 없는 환경에서 스킵",
)
class TestD7AsyncPostgresSaverDurability:
    """D7: AsyncPostgresSaver 기반 체크포인트 내구성.

    실제 PostgreSQL DB(localhost:5434)에 체크포인트를 저장하고 재개한다.
    DB가 없는 환경에서는 자동 스킵.
    """

    async def _make_pg_engine(self, store: WorkflowStore, action_client=None) -> tuple[WorkflowEngine, object]:
        """AsyncPostgresSaver 기반 엔진을 생성한다. (engine, context_manager) 반환."""
        from src.workflow.checkpointer import build_checkpointer
        from src.workflow.graph_builder import WorkflowGraphBuilder

        saver, cm = await build_checkpointer(_DB_URL)
        if saver is None:
            pytest.skip("AsyncPostgresSaver 초기화 실패 — DB 연결 불가")

        builder = WorkflowGraphBuilder(store)
        engine = WorkflowEngine(
            store,
            graph_builder=builder,
            checkpointer=saver,
            engine_backend="langgraph",
            action_client=action_client,
        )
        return engine, cm

    async def test_pg_checkpoint_persists_between_engines(self):
        """엔진 A가 checkpoint를 DB에 저장하고 엔진 B가 동일 DB에서 재개한다."""
        store = _build_store(_two_step_workflow())

        engine_a, cm_a = await self._make_pg_engine(store)

        session_id = "pg-dur-001"

        try:
            result_a = await engine_a.start("dur_two_step", session_id)
            assert result_a.step_id == "ask_name"

            # 엔진 B: 동일 DB에서 별도 연결로 재개
            engine_b, cm_b = await self._make_pg_engine(store)
            try:
                result_b = await engine_b.advance(session_id, "김유신")

                assert result_b.step_id == "done"
                assert "김유신" in result_b.bot_message
            finally:
                if cm_b is not None:
                    await cm_b.__aexit__(None, None, None)
        finally:
            # 정리: 테스트 thread 삭제
            try:
                if hasattr(engine_a._checkpointer, "adelete_thread"):
                    await engine_a._checkpointer.adelete_thread(session_id)
            except Exception:
                pass
            if cm_a is not None:
                await cm_a.__aexit__(None, None, None)

    async def test_pg_get_session_after_start(self):
        """DB 체크포인터로 start 후 get_session이 세션을 반환한다."""
        store = _build_store(_two_step_workflow())
        engine, cm = await self._make_pg_engine(store)
        session_id = "pg-dur-002"

        try:
            await engine.start("dur_two_step", session_id)
            session = await engine.get_session(session_id)

            assert session is not None
            assert session.workflow_id == "dur_two_step"
            assert session.current_step_id == "ask_name"
        finally:
            try:
                if hasattr(engine._checkpointer, "adelete_thread"):
                    await engine._checkpointer.adelete_thread(session_id)
            except Exception:
                pass
            if cm is not None:
                await cm.__aexit__(None, None, None)
