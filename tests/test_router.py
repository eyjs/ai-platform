"""Router 컴포넌트 테스트."""

from src.agent.profile import AgentProfile, HybridTrigger, IntentHint
from src.router.intent_classifier import IntentClassifier
from src.router.execution_plan import QuestionType
from src.router.mode_selector import ModeSelector


def test_mode_selector_agentic():
    selector = ModeSelector()
    profile = AgentProfile(id="test", name="Test", domain_scopes=[], mode="agentic")
    mode, wf_id = selector.select("질문", profile)
    assert mode == "agentic"
    assert wf_id is None


def test_mode_selector_workflow():
    selector = ModeSelector()
    profile = AgentProfile(
        id="test", name="Test", domain_scopes=[],
        mode="workflow", workflow_id="wf1",
    )
    mode, wf_id = selector.select("질문", profile)
    assert mode == "workflow"
    assert wf_id == "wf1"


def test_mode_selector_hybrid_trigger():
    selector = ModeSelector()
    profile = AgentProfile(
        id="test", name="Test", domain_scopes=[],
        mode="hybrid",
        hybrid_triggers=[
            HybridTrigger(
                keyword_patterns=["계약"],
                intent_types=["CONTRACT"],
                workflow_id="contract_wf",
            )
        ],
    )
    mode, wf_id = selector.select("계약 하고 싶어요", profile)
    assert mode == "workflow"
    assert wf_id == "contract_wf"


def test_mode_selector_hybrid_no_match():
    selector = ModeSelector()
    profile = AgentProfile(
        id="test", name="Test", domain_scopes=[],
        mode="hybrid",
        hybrid_triggers=[
            HybridTrigger(
                keyword_patterns=["계약"],
                intent_types=["CONTRACT"],
                workflow_id="contract_wf",
            )
        ],
    )
    mode, wf_id = selector.select("날씨 어때?", profile)
    assert mode == "agentic"
    assert wf_id is None
