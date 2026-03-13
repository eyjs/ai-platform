"""Workflow Engine 테스트.

순차적 챗봇 엔진의 핵심 시나리오를 검증한다.
"""

import pytest

from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.engine import WorkflowEngine, _render_template, _resolve_next, _validate_input
from src.workflow.state import WorkflowSession
from src.workflow.store import WorkflowStore


# --- 테스트용 워크플로우 정의 ---


def _build_store(*definitions: WorkflowDefinition) -> WorkflowStore:
    store = WorkflowStore()
    for d in definitions:
        store._definitions[d.id] = d
    return store


def _simple_workflow() -> WorkflowDefinition:
    """선택 → 입력 → 확인 → 완료."""
    return WorkflowDefinition(
        id="test_simple",
        name="테스트 워크플로우",
        steps=[
            WorkflowStep(id="ask_type", type="select", prompt="유형을 선택하세요.",
                         save_as="type", options=["A", "B"],
                         branches={"A": "ask_name", "B": "ask_name"}),
            WorkflowStep(id="ask_name", type="input", prompt="이름을 입력하세요.",
                         save_as="name", next="confirm"),
            WorkflowStep(id="confirm", type="confirm",
                         prompt="확인해주세요. (예/아니오)", save_as="ok",
                         branches={"예": "done", "아니오": "ask_type"}),
            WorkflowStep(id="done", type="message",
                         prompt="{{name}}님, 완료되었습니다."),
        ],
    )


def _branching_workflow() -> WorkflowDefinition:
    """분기 테스트용."""
    return WorkflowDefinition(
        id="test_branch",
        name="분기 테스트",
        steps=[
            WorkflowStep(id="start", type="select", prompt="선택하세요.",
                         save_as="choice", options=["X", "Y"],
                         branches={"X": "path_x", "Y": "path_y"}),
            WorkflowStep(id="path_x", type="message", prompt="X 경로입니다."),
            WorkflowStep(id="path_y", type="message", prompt="Y 경로입니다."),
        ],
    )


# --- 기본 플로우 ---


class TestWorkflowEngine:

    def test_start_returns_first_step(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        result = engine.start("test_simple", "s1")
        assert "유형을 선택하세요" in result.bot_message
        assert result.options == ["A", "B"]
        assert not result.completed

    def test_advance_collects_data(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        engine.start("test_simple", "s1")

        result = engine.advance("s1", "A")
        assert "이름을 입력하세요" in result.bot_message
        assert result.collected["type"] == "A"

    def test_full_flow_happy_path(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        engine.start("test_simple", "s1")

        engine.advance("s1", "A")           # select → ask_name
        engine.advance("s1", "홍길동")       # input → confirm
        result = engine.advance("s1", "예")  # confirm → done

        assert result.completed
        assert "홍길동" in result.bot_message
        assert result.collected["name"] == "홍길동"
        assert result.collected["type"] == "A"

    def test_confirm_reject_loops_back(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        engine.start("test_simple", "s1")

        engine.advance("s1", "A")
        engine.advance("s1", "홍길동")
        result = engine.advance("s1", "아니오")  # 다시 처음으로

        assert "유형을 선택하세요" in result.bot_message
        assert not result.completed

    def test_completed_workflow_returns_done(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        engine.start("test_simple", "s1")
        engine.advance("s1", "A")
        engine.advance("s1", "테스트")
        engine.advance("s1", "예")

        result = engine.advance("s1", "추가 입력")
        assert result.completed
        assert "이미 완료" in result.bot_message


# --- 분기 ---


class TestBranching:

    def test_branch_x(self):
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        engine.start("test_branch", "s1")
        result = engine.advance("s1", "X")
        assert "X 경로" in result.bot_message
        assert result.completed

    def test_branch_y(self):
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        engine.start("test_branch", "s1")
        result = engine.advance("s1", "Y")
        assert "Y 경로" in result.bot_message
        assert result.completed

    def test_branch_by_number(self):
        """번호(1, 2)로도 선택 가능."""
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        engine.start("test_branch", "s1")
        result = engine.advance("s1", "2")  # Y
        assert "Y 경로" in result.bot_message

    def test_branch_partial_match(self):
        """부분 문자열 매칭."""
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        engine.start("test_branch", "s1")
        result = engine.advance("s1", "X 선택할게요")
        assert "X 경로" in result.bot_message


# --- 입력 검증 ---


class TestValidation:

    def test_phone_valid(self):
        step = WorkflowStep(id="t", type="input", validation="phone")
        assert _validate_input(step, "010-1234-5678") == ""

    def test_phone_invalid(self):
        step = WorkflowStep(id="t", type="input", validation="phone")
        assert "전화번호" in _validate_input(step, "abc")

    def test_number_valid(self):
        step = WorkflowStep(id="t", type="input", validation="number")
        assert _validate_input(step, "42") == ""

    def test_number_invalid(self):
        step = WorkflowStep(id="t", type="input", validation="number")
        assert "숫자" in _validate_input(step, "세 명")

    def test_date_valid(self):
        step = WorkflowStep(id="t", type="input", validation="date")
        assert _validate_input(step, "2026-04-15") == ""

    def test_date_invalid(self):
        step = WorkflowStep(id="t", type="input", validation="date")
        assert "날짜" in _validate_input(step, "4월 15일")

    def test_email_valid(self):
        step = WorkflowStep(id="t", type="input", validation="email")
        assert _validate_input(step, "user@example.com") == ""

    def test_no_validation(self):
        step = WorkflowStep(id="t", type="input")
        assert _validate_input(step, "아무거나") == ""


# --- 템플릿 렌더링 ---


class TestTemplateRendering:

    def test_render_simple(self):
        assert _render_template("{{name}}님 안녕", {"name": "홍길동"}) == "홍길동님 안녕"

    def test_render_multiple(self):
        result = _render_template("{{a}}+{{b}}", {"a": "1", "b": "2"})
        assert result == "1+2"

    def test_render_missing_key(self):
        result = _render_template("{{name}}님", {})
        assert result == "{{name}}님"


# --- 다음 스텝 결정 ---


class TestResolveNext:

    def test_exact_match(self):
        step = WorkflowStep(id="t", type="select", branches={"A": "s1", "B": "s2"})
        assert _resolve_next(step, "A") == "s1"

    def test_case_insensitive(self):
        step = WorkflowStep(id="t", type="select", branches={"Yes": "s1", "No": "s2"})
        assert _resolve_next(step, "yes") == "s1"

    def test_number_index(self):
        step = WorkflowStep(id="t", type="select", branches={"A": "s1", "B": "s2"})
        assert _resolve_next(step, "2") == "s2"

    def test_fallback_to_next(self):
        step = WorkflowStep(id="t", type="select", branches={"A": "s1"}, next="default")
        assert _resolve_next(step, "unknown") == "default"

    def test_no_branches_uses_next(self):
        step = WorkflowStep(id="t", type="input", next="s2")
        assert _resolve_next(step, "anything") == "s2"


# --- WorkflowStore ---


class TestWorkflowStore:

    @pytest.mark.asyncio
    async def test_load_from_directory(self):
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        assert store.count >= 2
        assert store.get("insurance_contract") is not None
        assert store.get("camping_reservation") is not None

    @pytest.mark.asyncio
    async def test_load_missing_directory(self):
        store = WorkflowStore()
        await store.load_from_directory("/nonexistent/path")
        assert store.count == 0


# --- 에러 케이스 ---


class TestErrors:

    def test_start_unknown_workflow(self):
        engine = WorkflowEngine(WorkflowStore())
        with pytest.raises(Exception, match="찾을 수 없습니다"):
            engine.start("nonexistent", "s1")

    def test_advance_no_session(self):
        engine = WorkflowEngine(WorkflowStore())
        with pytest.raises(Exception, match="세션"):
            engine.advance("nonexistent", "hello")

    def test_cancel_session(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        engine.start("test_simple", "s1")
        assert engine.cancel("s1") is True
        assert engine.get_session("s1") is None


# --- YAML 시드 통합 테스트 ---


class TestYAMLSeeds:

    @pytest.mark.asyncio
    async def test_insurance_contract_full_flow(self):
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        engine = WorkflowEngine(store)

        # 시작
        result = engine.start("insurance_contract", "s1")
        assert "보험" in result.bot_message

        # 보험 종류 선택
        result = engine.advance("s1", "자동차보험")
        assert "연식" in result.bot_message or "출고" in result.bot_message

        # 차량 연식
        result = engine.advance("s1", "2022")
        assert "성함" in result.bot_message

        # 이름
        result = engine.advance("s1", "홍길동")
        assert "전화번호" in result.bot_message
        assert "홍길동" in result.bot_message  # 템플릿 렌더링

        # 전화번호
        result = engine.advance("s1", "010-1234-5678")
        assert "확인" in result.bot_message or "진행" in result.bot_message
        assert "홍길동" in result.bot_message  # 수집 데이터 요약

        # 확인
        result = engine.advance("s1", "예")
        assert result.completed
        assert "홍길동" in result.bot_message
        assert "자동차보험" in result.bot_message

    @pytest.mark.asyncio
    async def test_camping_reservation_full_flow(self):
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        engine = WorkflowEngine(store)

        result = engine.start("camping_reservation", "s2")
        assert "캠핑" in result.bot_message

        result = engine.advance("s2", "글램핑")
        assert "날짜" in result.bot_message

        result = engine.advance("s2", "2026-04-15")
        assert "박" in result.bot_message

        result = engine.advance("s2", "2박")
        assert "인원" in result.bot_message

        result = engine.advance("s2", "4")
        assert "성함" in result.bot_message

        result = engine.advance("s2", "김철수")
        assert "연락처" in result.bot_message

        result = engine.advance("s2", "010-9876-5432")
        assert "진행" in result.bot_message

        result = engine.advance("s2", "예")
        assert result.completed
        assert "김철수" in result.bot_message
        assert "글램핑" in result.bot_message
        assert "2박" in result.bot_message
