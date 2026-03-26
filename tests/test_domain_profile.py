"""AgentProfile domain 레이어 임포트 검증."""

from src.domain.agent_profile import AgentProfile, HybridTrigger, IntentHint, ToolRef


def test_agent_profile_in_domain():
    """AgentProfile이 domain 레이어에서 임포트 가능해야 한다."""
    profile = AgentProfile(id="test", name="Test")
    assert profile.id == "test"
    assert profile.name == "Test"
    assert profile.tool_names == []


def test_tool_ref_frozen():
    ref = ToolRef(name="rag_search")
    assert ref.name == "rag_search"
    assert ref.config == {}


def test_intent_hint_frozen():
    hint = IntentHint(name="claim", patterns=["청구"], description="보험금 청구")
    assert hint.name == "claim"


def test_hybrid_trigger_frozen():
    trigger = HybridTrigger(
        keyword_patterns=["견적"], intent_types=["quote"], workflow_id="w1",
    )
    assert trigger.workflow_id == "w1"


def test_backward_compat_import():
    """기존 src.agent.profile 경로로도 임포트 가능해야 한다."""
    from src.agent.profile import AgentProfile as AP
    assert AP is AgentProfile


def test_backward_compat_agent_context():
    """기존 src.tools.base 경로로도 AgentContext 임포트 가능해야 한다."""
    from src.tools.base import AgentContext
    from src.domain.agent_context import AgentContext as AC
    assert AgentContext is AC
