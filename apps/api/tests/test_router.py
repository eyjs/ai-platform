"""Router 컴포넌트 테스트."""

from src.agent.profile import AgentMode, AgentProfile, HybridTrigger
from src.router.execution_plan import QuestionType
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
