"""Router 컴포넌트 테스트."""

from src.agent.profile import AgentMode, AgentProfile, HybridTrigger
from src.domain.execution_plan import QuestionType
from src.router.mode_selector import ModeSelector
from src.router.semantic_classifier import ClassifyResult


class _StubClassifier:
    """ModeSelector용 스텁 — 지정 label 반환. 호출 횟수 기록(fast-path 검증)."""

    def __init__(self, label=None, confidence=0.9):
        self._label = label
        self._confidence = confidence
        self.calls = 0

    async def classify(self, query, candidates, *, context="", threshold=0.6):
        self.calls += 1
        return ClassifyResult(self._label, self._confidence)


def _hybrid_profile():
    return AgentProfile(
        id="test", name="Test", domain_scopes=[],
        mode=AgentMode.HYBRID,
        hybrid_triggers=[
            HybridTrigger(
                keyword_patterns=["계약"],
                intent_types=["CONTRACT"],
                workflow_id="contract_wf",
                description="보험 계약/가입 신청 절차",
            )
        ],
    )


async def test_mode_selector_agentic():
    mode, wf_id = await ModeSelector().select(
        "질문", AgentProfile(id="t", name="T", domain_scopes=[], mode=AgentMode.AGENTIC),
    )
    assert mode == "agentic"
    assert wf_id is None


async def test_mode_selector_workflow():
    profile = AgentProfile(
        id="t", name="T", domain_scopes=[], mode=AgentMode.WORKFLOW, workflow_id="wf1",
    )
    mode, wf_id = await ModeSelector().select("질문", profile)
    assert mode == "workflow"
    assert wf_id == "wf1"


async def test_mode_selector_keyword_fastpath_skips_llm():
    """키워드 매칭은 LLM 미호출 fast-path."""
    stub = _StubClassifier(label="contract_wf")
    mode, wf_id = await ModeSelector(stub).select("계약 하고 싶어요", _hybrid_profile())
    assert mode == "workflow"
    assert wf_id == "contract_wf"
    assert stub.calls == 0


async def test_mode_selector_llm_entry_on_keyword_miss():
    """키워드 없는 자유입력 → LLM 의미 진입으로 워크플로우 결정."""
    stub = _StubClassifier(label="contract_wf")
    mode, wf_id = await ModeSelector(stub).select(
        "그거 신청하려면 뭐부터 해야 돼?", _hybrid_profile(),
        question_type=QuestionType.STANDALONE,
    )
    assert mode == "workflow"
    assert wf_id == "contract_wf"
    assert stub.calls == 1


async def test_mode_selector_llm_entry_none_agentic():
    """분류기가 NONE이면 일반챗."""
    stub = _StubClassifier(label=None)
    mode, wf_id = await ModeSelector(stub).select(
        "날씨 어때?", _hybrid_profile(), question_type=QuestionType.STANDALONE,
    )
    assert mode == "agentic"
    assert wf_id is None


async def test_mode_selector_skip_llm_on_greeting():
    """인사/시스템 질문은 진입 LLM 스킵(haiku 절약)."""
    stub = _StubClassifier(label="contract_wf")
    mode, wf_id = await ModeSelector(stub).select(
        "안녕", _hybrid_profile(), question_type=QuestionType.GREETING,
    )
    assert mode == "agentic"
    assert stub.calls == 0


async def test_mode_selector_no_classifier_miss_agentic():
    """분류기 미주입 + 키워드 미스 → 기존 동작(agentic)."""
    mode, wf_id = await ModeSelector().select("날씨 어때?", _hybrid_profile())
    assert mode == "agentic"
    assert wf_id is None


# --- W3: 라우터 폴백 예외 좁히기 (LLM 결함만 폴백, 버그는 전파) ---


async def test_run_l0_recovers_aierror():
    """L0: AIError(LLM 결함)는 passthrough 폴백으로 복구된다(기존 동작)."""
    from unittest.mock import AsyncMock, MagicMock
    import pytest
    from src.router.ai_router import AIRouter
    from src.common.exceptions import AIError

    router = AIRouter.__new__(AIRouter)  # __init__ 우회(실 LLM 컴포넌트 빌드 방지)
    router._resolver = MagicMock()
    router._resolver.resolve = AsyncMock(
        side_effect=AIError("llm parse 실패", layer="ROUTER", error_code="ERR_ROUTER_L0"),
    )

    resolved, resolution = await router._run_l0("내 질문", [])
    assert resolved == "내 질문"
    assert resolution.method == "fallback"


async def test_run_l0_propagates_programming_bug():
    """L0: TypeError 등 비-LLM 버그는 폴백으로 가리지 않고 전파한다(W3)."""
    from unittest.mock import AsyncMock, MagicMock
    import pytest
    from src.router.ai_router import AIRouter

    router = AIRouter.__new__(AIRouter)
    router._resolver = MagicMock()
    router._resolver.resolve = AsyncMock(side_effect=TypeError("코드 버그"))

    with pytest.raises(TypeError):
        await router._run_l0("내 질문", [])
