"""AgentProfile 데이터 모델 테스트."""

from src.agent.profile import AgentProfile, HybridTrigger, IntentHint, ToolRef


def test_agent_profile_defaults():
    profile = AgentProfile(id="test", name="Test", domain_scopes=[])
    assert profile.mode == "agentic"
    assert profile.security_level_max == "PUBLIC"
    assert profile.response_policy == "balanced"
    assert profile.memory_type == "short"
    assert profile.tools == []
    assert profile.guardrails == []


def test_agent_profile_frozen():
    profile = AgentProfile(id="test", name="Test", domain_scopes=["보험"])
    try:
        profile.id = "changed"
        assert False, "Should be frozen"
    except AttributeError:
        pass


def test_tool_ref():
    ref = ToolRef(name="rag_search", config={"max_vector_chunks": 3})
    assert ref.name == "rag_search"
    assert ref.config["max_vector_chunks"] == 3


def test_intent_hint():
    hint = IntentHint(
        name="CONTRACT", patterns=["계약", "가입"],
        description="계약 요청", route_to="workflow",
    )
    assert hint.route_to == "workflow"
    assert "계약" in hint.patterns


def test_hybrid_trigger():
    trigger = HybridTrigger(
        keyword_patterns=["계약"], intent_types=["CONTRACT"],
        workflow_id="insurance_contract",
    )
    assert trigger.workflow_id == "insurance_contract"
