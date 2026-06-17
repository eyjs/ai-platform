"""Workflow Engine 테스트.

순차적 챗봇 엔진의 핵심 시나리오를 검증한다.
async 전환 완료: 모든 엔진 메서드가 async.
"""

import pytest

from src.router.semantic_classifier import SemanticClassifier
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.engine import WorkflowEngine, _resolve_next, _validate_input
from src.workflow.state import WorkflowSession
from src.workflow.store import WorkflowStore
from src.workflow.template import render_template


# --- 테스트용 워크플로우 정의 ---


def _build_store(*definitions: WorkflowDefinition) -> WorkflowStore:
    store = WorkflowStore()
    for d in definitions:
        store._cache[d.id] = d
    return store


class _BranchStubLLM:
    """SemanticClassifier용 스텁 — 지정 label을 반환(자유입력 분기 테스트). 호출 횟수 기록."""

    def __init__(self, label: str, confidence: float = 0.9) -> None:
        self._label = label
        self._confidence = confidence
        self.calls = 0

    async def generate_json(self, prompt: str, system: str = "") -> dict:
        self.calls += 1
        return {"label": self._label, "confidence": self._confidence}


def _simple_workflow() -> WorkflowDefinition:
    """선택 -> 입력 -> 확인 -> 완료."""
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


def _action_workflow() -> WorkflowDefinition:
    """action step 테스트용."""
    return WorkflowDefinition(
        id="test_action",
        name="액션 테스트",
        steps=[
            WorkflowStep(id="ask_name", type="input", prompt="이름을 입력하세요.",
                         save_as="name", next="submit"),
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


def _escape_keywords_workflow() -> WorkflowDefinition:
    """워크플로우별 escape_keywords 테스트용."""
    return WorkflowDefinition(
        id="test_custom_escape",
        name="커스텀 이탈 테스트",
        escape_keywords=["stop", "끝내기"],
        steps=[
            WorkflowStep(id="ask", type="input", prompt="입력하세요.",
                         save_as="val", next="done"),
            WorkflowStep(id="done", type="message", prompt="완료."),
        ],
    )


# --- 기본 플로우 ---


class TestWorkflowEngine:

    async def test_start_returns_first_step(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        result = await engine.start("test_simple", "s1")
        assert "유형을 선택하세요" in result.bot_message
        assert result.options == ["A", "B"]
        assert not result.completed

    async def test_advance_collects_data(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        await engine.start("test_simple", "s1")

        result = await engine.advance("s1", "A")
        assert "이름을 입력하세요" in result.bot_message
        assert result.collected["type"] == "A"

    async def test_full_flow_happy_path(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        await engine.start("test_simple", "s1")

        await engine.advance("s1", "A")           # select -> ask_name
        await engine.advance("s1", "홍길동")       # input -> confirm
        result = await engine.advance("s1", "예")  # confirm -> done

        assert result.completed
        assert "홍길동" in result.bot_message
        assert result.collected["name"] == "홍길동"
        assert result.collected["type"] == "A"

    async def test_confirm_reject_loops_back(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        await engine.start("test_simple", "s1")

        await engine.advance("s1", "A")
        await engine.advance("s1", "홍길동")
        result = await engine.advance("s1", "아니오")  # 다시 처음으로

        assert "유형을 선택하세요" in result.bot_message
        assert not result.completed

    async def test_completed_workflow_returns_done(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        await engine.start("test_simple", "s1")
        await engine.advance("s1", "A")
        await engine.advance("s1", "테스트")
        await engine.advance("s1", "예")

        result = await engine.advance("s1", "추가 입력")
        assert result.completed
        assert "이미 완료" in result.bot_message


# --- 분기 ---


class TestBranching:

    async def test_branch_x(self):
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        await engine.start("test_branch", "s1")
        result = await engine.advance("s1", "X")
        assert "X 경로" in result.bot_message
        assert result.completed

    async def test_branch_y(self):
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        await engine.start("test_branch", "s1")
        result = await engine.advance("s1", "Y")
        assert "Y 경로" in result.bot_message
        assert result.completed

    async def test_branch_by_number(self):
        """번호(1, 2)로도 선택 가능."""
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        await engine.start("test_branch", "s1")
        result = await engine.advance("s1", "2")  # Y
        assert "Y 경로" in result.bot_message

    async def test_branch_freetext_without_classifier_reprompts(self):
        """분류기 없으면 자유입력은 부분문자열 오매칭 대신 안전하게 재안내(종료 아님)."""
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        await engine.start("test_branch", "s1")
        result = await engine.advance("s1", "음 글쎄 X 쪽인가")
        assert not result.completed
        assert result.step_id == "start"  # 같은 스텝 재안내

    async def test_branch_freetext_classified_by_llm(self):
        """분류기 주입 시 자유입력을 의미로 분기 + collected를 정규 옵션으로 저장."""
        engine = WorkflowEngine(
            _build_store(_branching_workflow()),
            classifier=SemanticClassifier(_BranchStubLLM("X")),
        )
        await engine.start("test_branch", "s1")
        result = await engine.advance("s1", "고민 끝에 X로 갈래")
        assert "X 경로" in result.bot_message
        assert result.collected["choice"] == "X"  # 원시 입력 아닌 정규 분기키

    async def test_branch_classifier_none_reprompts(self):
        """분류기가 NONE이면 재안내(오분류 강행 안 함)."""
        engine = WorkflowEngine(
            _build_store(_branching_workflow()),
            classifier=SemanticClassifier(_BranchStubLLM("NONE")),
        )
        await engine.start("test_branch", "s1")
        result = await engine.advance("s1", "무슨 말인지")
        assert not result.completed
        assert result.step_id == "start"

    async def test_branch_button_exact_skips_classifier(self):
        """정확 옵션(버튼)은 분류기 LLM 미호출 fast-path."""
        stub = _BranchStubLLM("Y")
        engine = WorkflowEngine(
            _build_store(_branching_workflow()),
            classifier=SemanticClassifier(stub),
        )
        await engine.start("test_branch", "s1")
        result = await engine.advance("s1", "X")  # 정확 옵션
        assert "X 경로" in result.bot_message
        assert stub.calls == 0  # 자유입력 아니므로 LLM 미호출

    async def test_select_unmatched_freetext_reprompts_not_completed(self):
        """회귀: select 스텝에 분기와 안 맞는 자유텍스트가 오면 워크플로우를 종료하지 않고
        같은 스텝을 다시 안내한다. (이전: '워크플로우가 완료되었습니다'로 조기종료되던 버그)"""
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        await engine.start("test_branch", "s1")
        result = await engine.advance("s1", "둘 다 궁금해서 왔어")
        assert not result.completed
        assert not result.escaped
        assert result.step_id == "start"  # 같은 스텝에 머무름
        assert result.options == ["X", "Y"]  # 선택지 재노출
        assert "choice" not in result.collected  # 미매칭 입력은 저장되지 않음(롤백)

    async def test_select_unmatched_escapes_after_max_retries(self):
        """회귀: 미매칭이 max_retries(3)만큼 누적되면 그제서야 종료(무한 재안내 방지)."""
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        await engine.start("test_branch", "s1")
        r1 = await engine.advance("s1", "아무거나1")
        r2 = await engine.advance("s1", "아무거나2")
        assert not r1.completed and not r2.completed  # 2회까지는 재안내
        r3 = await engine.advance("s1", "아무거나3")  # 3회째 = max_retries 도달
        assert r3.completed and r3.escaped

    async def test_select_match_after_unmatched_recovers(self):
        """미매칭 재안내 후 올바른 선택을 하면 정상 진행한다(retry 카운터 리셋 확인)."""
        engine = WorkflowEngine(_build_store(_branching_workflow()))
        await engine.start("test_branch", "s1")
        await engine.advance("s1", "엉뚱한 입력")  # 1회 미매칭 → 재안내
        result = await engine.advance("s1", "X")  # 올바른 선택
        assert "X 경로" in result.bot_message
        assert result.completed


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
        assert render_template("{{name}}님 안녕", {"name": "홍길동"}) == "홍길동님 안녕"

    def test_render_multiple(self):
        result = render_template("{{a}}+{{b}}", {"a": "1", "b": "2"})
        assert result == "1+2"

    def test_render_missing_key(self):
        result = render_template("{{name}}님", {})
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

    async def test_load_from_directory(self):
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        assert store.count >= 2
        assert store.get("insurance_contract") is not None
        assert store.get("camping_reservation") is not None

    async def test_load_missing_directory(self):
        store = WorkflowStore()
        await store.load_from_directory("/nonexistent/path")
        assert store.count == 0


# --- 에러 케이스 ---


class TestErrors:

    async def test_start_unknown_workflow(self):
        engine = WorkflowEngine(WorkflowStore())
        with pytest.raises(Exception, match="찾을 수 없습니다"):
            await engine.start("nonexistent", "s1")

    async def test_advance_no_session(self):
        engine = WorkflowEngine(WorkflowStore())
        with pytest.raises(Exception, match="세션"):
            await engine.advance("nonexistent", "hello")

    async def test_cancel_session(self):
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        await engine.start("test_simple", "s1")
        assert await engine.cancel("s1") is True
        assert await engine.get_session("s1") is None


# --- 이탈 감지 (Escape Hatch) ---


class TestEscapeHatch:

    async def test_escape_cancels_workflow(self):
        """'취소' 입력 시 워크플로우가 종료되고 escaped=True."""
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        await engine.start("test_simple", "s1")
        await engine.advance("s1", "A")  # select -> ask_name

        result = await engine.advance("s1", "취소")
        assert result.completed
        assert result.escaped
        assert "취소" in result.bot_message

    async def test_escape_keywords_all(self):
        """모든 이탈 키워드가 동작한다."""
        for keyword in ["취소", "처음으로", "나가기", "중단", "그만", "exit", "cancel", "quit"]:
            engine = WorkflowEngine(_build_store(_simple_workflow()))
            await engine.start("test_simple", f"s_{keyword}")
            result = await engine.advance(f"s_{keyword}", keyword)
            assert result.escaped, f"'{keyword}' should trigger escape"

    async def test_escape_preserves_collected(self):
        """이탈 시에도 수집된 데이터가 보존된다."""
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        await engine.start("test_simple", "s1")
        await engine.advance("s1", "A")  # type=A 수집됨

        result = await engine.advance("s1", "그만")
        assert result.escaped
        assert result.collected["type"] == "A"

    async def test_graceful_fallback_after_3_retries(self):
        """검증 실패 3회 시 자동 취소 (Graceful Fallback)."""
        wf = WorkflowDefinition(
            id="test_retry",
            name="리트라이 테스트",
            steps=[
                WorkflowStep(id="ask_phone", type="input", prompt="전화번호를 입력하세요.",
                             save_as="phone", validation="phone", next="done"),
                WorkflowStep(id="done", type="message", prompt="완료."),
            ],
        )
        engine = WorkflowEngine(_build_store(wf))
        await engine.start("test_retry", "s1")

        # 1회 실패
        result = await engine.advance("s1", "abc")
        assert "전화번호" in result.bot_message
        assert not result.completed

        # 2회 실패
        result = await engine.advance("s1", "xyz")
        assert not result.completed

        # 3회 실패 -> 자동 취소
        result = await engine.advance("s1", "!!!")
        assert result.completed
        assert result.escaped
        assert "취소" in result.bot_message

    async def test_retry_resets_on_success(self):
        """스텝 통과 후 retry 카운터가 리셋된다."""
        engine = WorkflowEngine(_build_store(_simple_workflow()))
        await engine.start("test_simple", "s1")

        # select 스텝은 validation 없으므로 바로 통과
        await engine.advance("s1", "A")
        session = await engine.get_session("s1")
        assert session.retry_count == 0

    async def test_escape_blocked_by_policy(self):
        """escape_policy='block'이면 이탈 키워드가 무시된다."""
        wf = WorkflowDefinition(
            id="test_block",
            name="블록 테스트",
            escape_policy="block",
            steps=[
                WorkflowStep(id="ask", type="input", prompt="입력하세요.",
                             save_as="val", next="done"),
                WorkflowStep(id="done", type="message", prompt="완료."),
            ],
        )
        engine = WorkflowEngine(_build_store(wf))
        await engine.start("test_block", "s1")

        result = await engine.advance("s1", "취소")
        # escape_policy=block이므로 취소가 아닌 일반 입력으로 처리
        assert not result.escaped
        assert result.completed  # "취소"가 val로 저장되고 done으로 진행


# --- 워크플로우별 escape_keywords ---


class TestPerWorkflowEscapeKeywords:

    async def test_custom_keywords_trigger_escape(self):
        """워크플로우에 정의된 커스텀 이탈 키워드가 동작한다."""
        engine = WorkflowEngine(_build_store(_escape_keywords_workflow()))
        await engine.start("test_custom_escape", "s1")

        result = await engine.advance("s1", "stop")
        assert result.escaped
        assert result.completed

    async def test_custom_keywords_second(self):
        """커스텀 이탈 키워드 두 번째도 동작한다."""
        engine = WorkflowEngine(_build_store(_escape_keywords_workflow()))
        await engine.start("test_custom_escape", "s1")

        result = await engine.advance("s1", "끝내기")
        assert result.escaped
        assert result.completed

    async def test_global_keywords_not_active_when_custom_defined(self):
        """커스텀 escape_keywords가 정의되면 전역 키워드는 동작하지 않는다."""
        engine = WorkflowEngine(_build_store(_escape_keywords_workflow()))
        await engine.start("test_custom_escape", "s1")

        # "취소"는 커스텀 목록에 없으므로 일반 입력으로 처리
        result = await engine.advance("s1", "취소")
        assert not result.escaped
        assert result.completed  # "취소"가 val로 저장되고 done으로 진행

    async def test_empty_escape_keywords_uses_global(self):
        """escape_keywords가 빈 리스트이면 전역 키워드가 적용된다."""
        wf = WorkflowDefinition(
            id="test_global_escape",
            name="전역 이탈 테스트",
            escape_keywords=[],  # 빈 리스트 -> 전역 사용
            steps=[
                WorkflowStep(id="ask", type="input", prompt="입력하세요.",
                             save_as="val", next="done"),
                WorkflowStep(id="done", type="message", prompt="완료."),
            ],
        )
        engine = WorkflowEngine(_build_store(wf))
        await engine.start("test_global_escape", "s1")

        result = await engine.advance("s1", "취소")
        assert result.escaped


# --- Action Step 테스트 (no client) ---


class TestActionStepNoClient:

    async def test_action_without_client_returns_error(self):
        """action_client 미주입 시 에러 메시지를 반환한다."""
        engine = WorkflowEngine(_build_store(_action_workflow()), action_client=None)
        await engine.start("test_action", "s1")
        result = await engine.advance("s1", "홍길동")
        # action step에 도달하지만 client가 없으므로 에러
        assert result.completed
        assert "비활성화" in result.bot_message or "오류" in result.bot_message

    async def test_action_without_endpoint_returns_error(self):
        """endpoint도 profile endpoint도 없으면 에러."""
        from unittest.mock import AsyncMock, MagicMock
        mock_client = MagicMock()
        mock_client.call = AsyncMock()

        # endpoint가 없는 action step
        wf = WorkflowDefinition(
            id="test_no_ep",
            name="엔드포인트 없음",
            steps=[
                WorkflowStep(id="ask", type="input", prompt="이름?",
                             save_as="name", next="act"),
                WorkflowStep(id="act", type="action", prompt="처리중...",
                             endpoint="", http_method="POST",
                             on_error_message="엔드포인트 없음 에러",
                             next="done"),
                WorkflowStep(id="done", type="message", prompt="끝"),
            ],
        )
        engine = WorkflowEngine(_build_store(wf), action_client=mock_client)
        await engine.start("test_no_ep", "s1")
        result = await engine.advance("s1", "테스트")
        assert result.completed
        assert "엔드포인트" in result.bot_message or "엔드포인트 없음 에러" in result.bot_message
        mock_client.call.assert_not_called()


# --- YAML 시드 통합 테스트 ---


class TestYAMLSeeds:

    async def test_insurance_contract_full_flow(self):
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        engine = WorkflowEngine(store)

        # 시작
        result = await engine.start("insurance_contract", "s1")
        assert "보험" in result.bot_message

        # 보험 종류 선택
        result = await engine.advance("s1", "자동차보험")
        assert "연식" in result.bot_message or "출고" in result.bot_message

        # 차량 연식
        result = await engine.advance("s1", "2022")
        assert "성함" in result.bot_message

        # 이름
        result = await engine.advance("s1", "홍길동")
        assert "전화번호" in result.bot_message
        assert "홍길동" in result.bot_message  # 템플릿 렌더링

        # 전화번호
        result = await engine.advance("s1", "010-1234-5678")
        assert "확인" in result.bot_message or "진행" in result.bot_message
        assert "홍길동" in result.bot_message  # 수집 데이터 요약

        # 확인 -> action step (no client이므로 에러로 종료)
        result = await engine.advance("s1", "예")
        # action_client가 없으므로 on_error_message 또는 비활성화 메시지
        assert result.completed

    async def test_insurance_contract_has_escape_keywords(self):
        """보험 워크플로우에 커스텀 escape_keywords가 적용된다."""
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        definition = store.get("insurance_contract")
        assert definition is not None
        assert "취소" in definition.escape_keywords
        assert "나가기" in definition.escape_keywords
        assert "cancel" in definition.escape_keywords

    async def test_insurance_escape_with_custom_keyword(self):
        """보험 워크플로우에서 커스텀 escape keyword로 이탈."""
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        engine = WorkflowEngine(store)

        await engine.start("insurance_contract", "s1")
        result = await engine.advance("s1", "나가기")
        assert result.escaped
        assert result.completed

    async def test_insurance_non_custom_keyword_not_escape(self):
        """보험 워크플로우에서 커스텀 목록 밖의 전역 키워드는 이탈하지 않는다."""
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        engine = WorkflowEngine(store)

        await engine.start("insurance_contract", "s1")
        # "exit"은 보험 워크플로우의 escape_keywords에 없다
        result = await engine.advance("s1", "exit")
        assert not result.escaped

    async def test_escape_mid_flow(self):
        """워크플로우 도중 이탈 후 재시작 불가 확인."""
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        engine = WorkflowEngine(store)

        await engine.start("camping_reservation", "s_escape")
        await engine.advance("s_escape", "글램핑")
        result = await engine.advance("s_escape", "취소")
        assert result.escaped
        assert result.collected["site_type"] == "글램핑"

        # 이탈 후 세션은 completed 상태
        result = await engine.advance("s_escape", "다시 시작")
        assert result.completed
        assert "이미 완료" in result.bot_message

    async def test_camping_reservation_full_flow(self):
        store = WorkflowStore()
        await store.load_from_directory("seeds/workflows")
        engine = WorkflowEngine(store)

        result = await engine.start("camping_reservation", "s2")
        assert "캠핑" in result.bot_message

        result = await engine.advance("s2", "글램핑")
        assert "날짜" in result.bot_message

        result = await engine.advance("s2", "2026-04-15")
        assert "박" in result.bot_message

        result = await engine.advance("s2", "2박")
        assert "인원" in result.bot_message

        result = await engine.advance("s2", "4")
        assert "성함" in result.bot_message

        result = await engine.advance("s2", "김철수")
        assert "연락처" in result.bot_message

        result = await engine.advance("s2", "010-9876-5432")
        assert "진행" in result.bot_message

        result = await engine.advance("s2", "예")
        assert result.completed
        assert "김철수" in result.bot_message
        assert "글램핑" in result.bot_message
        assert "2박" in result.bot_message
