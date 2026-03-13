"""Layer-Aware 예외 계층 테스트."""

import pytest

from src.common.exceptions import (
    AIError,
    AgentAIError,
    AppError,
    GatewayError,
    InfraError,
    PipelineError,
    RouterAIError,
    SafetyAIError,
    ToolAIError,
)


# --- 예외 계층 구조 ---


def test_ai_error_is_app_error():
    """AIError는 AppError의 하위 클래스."""
    err = AIError("test", layer="ROUTER", error_code="ERR_TEST_001")
    assert isinstance(err, AppError)


def test_infra_error_is_app_error():
    """InfraError는 AppError의 하위 클래스."""
    err = InfraError("test")
    assert isinstance(err, AppError)


def test_ai_error_not_infra():
    """AIError와 InfraError는 독립 계층."""
    err = AIError("test", layer="TOOL", error_code="ERR_TEST")
    assert not isinstance(err, InfraError)


def test_router_ai_error_is_ai_error():
    """RouterAIError → AIError → AppError 체인."""
    err = RouterAIError("LLM 파싱 실패")
    assert isinstance(err, AIError)
    assert isinstance(err, AppError)
    assert not isinstance(err, InfraError)


# --- 필드 검증 ---


def test_router_ai_error_defaults():
    """RouterAIError의 기본 필드값."""
    err = RouterAIError("test error")
    assert err.layer == "ROUTER"
    assert err.error_code == "ERR_ROUTER_001"
    assert err.component == ""
    assert err.details == {}
    assert str(err) == "test error"


def test_agent_ai_error_custom_fields():
    """AgentAIError에 커스텀 필드 전달."""
    err = AgentAIError(
        "LLM 스트리밍 중 토큰 생성 실패",
        error_code="ERR_AGENT_004",
        component="GraphExecutor",
        details={"model": "qwen3:8b", "tokens_generated": 42},
    )
    assert err.layer == "AGENT"
    assert err.error_code == "ERR_AGENT_004"
    assert err.component == "GraphExecutor"
    assert err.details["model"] == "qwen3:8b"


def test_infra_error_defaults():
    """InfraError의 기본 필드값."""
    err = InfraError("DB 커넥션 풀 고갈")
    assert err.layer == "INFRA"
    assert err.error_code == "ERR_INFRA_001"


def test_tool_ai_error():
    """ToolAIError 기본값."""
    err = ToolAIError("리랭킹 스코어 이상", component="RAGSearch")
    assert err.layer == "TOOL"
    assert err.error_code == "ERR_TOOL_001"
    assert err.component == "RAGSearch"


def test_safety_ai_error():
    """SafetyAIError 기본값."""
    err = SafetyAIError("Faithfulness 검증 실패")
    assert err.layer == "SAFETY"


def test_gateway_error():
    """GatewayError 기본값."""
    err = GatewayError("요청 파싱 실패")
    assert err.layer == "GATEWAY"


def test_pipeline_error():
    """PipelineError 기본값."""
    err = PipelineError("청킹 실패")
    assert err.layer == "PIPELINE"


# --- except 체인: AI 에러만 잡히는지 검증 ---


def test_catch_ai_error_catches_router():
    """except AIError로 RouterAIError를 잡을 수 있다."""
    with pytest.raises(AIError):
        raise RouterAIError("test")


def test_catch_ai_error_catches_agent():
    """except AIError로 AgentAIError를 잡을 수 있다."""
    with pytest.raises(AIError):
        raise AgentAIError("test")


def test_catch_ai_error_misses_infra():
    """except AIError로 InfraError는 잡히지 않는다."""
    with pytest.raises(InfraError):
        try:
            raise InfraError("DB 다운")
        except AIError:
            pytest.fail("InfraError가 AIError로 잡히면 안 됨")
