"""AgentProfile 데이터 모델 테스트."""

from src.agent.profile import AgentMode, AgentProfile, HybridTrigger, IntentHint, ToolRef


def test_agent_profile_defaults():
    profile = AgentProfile(id="test", name="Test", domain_scopes=[])
    assert profile.mode == AgentMode.AGENTIC
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


def test_tool_names_property():
    profile = AgentProfile(
        id="test", name="Test", domain_scopes=[],
        tools=[ToolRef(name="rag_search"), ToolRef(name="fact_lookup")],
    )
    assert profile.tool_names == ["rag_search", "fact_lookup"]


def test_intent_hint():
    hint = IntentHint(
        name="CONTRACT", patterns=["계약", "가입"],
        description="계약 요청",
    )
    assert hint.description == "계약 요청"
    assert "계약" in hint.patterns


def test_hybrid_trigger():
    trigger = HybridTrigger(
        keyword_patterns=["계약"], intent_types=["CONTRACT"],
        workflow_id="insurance_contract",
    )
    assert trigger.workflow_id == "insurance_contract"


def test_agent_mode_enum():
    assert AgentMode.AGENTIC.value == "agentic"
    assert AgentMode.WORKFLOW.value == "workflow"
    assert AgentMode.HYBRID.value == "hybrid"
    assert AgentMode("agentic") == AgentMode.AGENTIC
