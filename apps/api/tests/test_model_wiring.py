"""P0-2/3 모델 배선 테스트.

커버:
  1. resolve_model_alias 단위 테스트 — 백엔드별(anthropic/dev-ollama/dev-http/openai)
  2. 통합: ProfileStore.parse_profile → StrategyBuilder.build → plan.main_model/router_model
  3. GraphExecutor 오버라이드: stub ProviderFactory 주입, get_chat_model 호출 검증
  4. 회귀: main_model="" / settings=None / provider_factory=None → 기존 경로

AAA 패턴. 외부 의존성(실제 LLM/DB)만 모킹.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. resolve_model_alias 단위 테스트
# ---------------------------------------------------------------------------


class TestResolveModelAliasAnthropic:
    """anthropic provider_mode 에서의 alias 해석."""

    def _settings(self):
        """anthropic 모드 Settings 인스턴스."""
        from src.config import Settings
        return Settings(
            provider_mode="anthropic",
            anthropic_api_key="sk-test",
            anthropic_main_model="claude-haiku-4-5",
            anthropic_router_model="claude-haiku-4-5",
        )

    def test_haiku_resolves_to_haiku_id(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("haiku", s) == "claude-haiku-4-5"

    def test_sonnet_resolves_to_sonnet_id(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("sonnet", s) == "claude-sonnet-4-5"

    def test_opus_resolves_to_opus_id(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("opus", s) == "claude-opus-4-5"

    def test_concrete_claude_id_passes_through(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("claude-opus-4-5", s) == "claude-opus-4-5"

    def test_unknown_alias_returns_empty(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("gpt-4o", s) == ""

    def test_empty_alias_returns_empty(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("", s) == ""


class TestResolveModelAliasDevelopmentOllama:
    """development 모드 + no server_url (ollama) 에서의 alias 해석."""

    def _settings(self):
        from src.config import Settings
        return Settings(
            provider_mode="development",
            main_llm_server_url="",
            router_model="qwen3.5:9b",
            main_model="qwen3.5:27b",
        )

    def test_haiku_resolves_to_router_model(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("haiku", s) == "qwen3.5:9b"

    def test_sonnet_resolves_to_main_model(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("sonnet", s) == "qwen3.5:27b"

    def test_opus_resolves_to_main_model(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("opus", s) == "qwen3.5:27b"

    def test_concrete_id_passes_through(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("qwen3.5:27b", s) == "qwen3.5:27b"

    def test_unknown_alias_returns_empty(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        # "ultra"는 _KNOWN_ALIASES에 없으므로 구체 ID로 통과
        result = resolve_model_alias("ultra", s)
        assert result == "ultra"  # 구체 ID로 취급

    def test_empty_returns_empty(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("", s) == ""


class TestResolveModelAliasDevelopmentHttp:
    """development 모드 + server_url 있음 (http) 에서의 alias 해석."""

    def _settings(self):
        from src.config import Settings
        return Settings(
            provider_mode="development",
            main_llm_server_url="http://localhost:8104",
            router_model="mlx-community/Qwen3.5-9B-4bit",
            main_model="mlx-community/Qwen3.5-27B-4bit",
        )

    def test_haiku_resolves_to_router_model(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("haiku", s) == "mlx-community/Qwen3.5-9B-4bit"

    def test_sonnet_resolves_to_main_model(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("sonnet", s) == "mlx-community/Qwen3.5-27B-4bit"

    def test_concrete_id_passes_through(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("mlx-community/Qwen3.5-27B-4bit", s) == "mlx-community/Qwen3.5-27B-4bit"


class TestResolveModelAliasOpenAI:
    """openai provider_mode 에서의 alias 해석."""

    def _settings(self):
        from src.config import Settings
        return Settings(
            provider_mode="openai",
            openai_api_key="sk-test",
            prod_llm_model="gpt-4o-mini",
        )

    def test_haiku_resolves_to_gpt4o_mini(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("haiku", s) == "gpt-4o-mini"

    def test_sonnet_resolves_to_prod_model(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("sonnet", s) == "gpt-4o-mini"

    def test_opus_resolves_to_gpt4o(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("opus", s) == "gpt-4o"

    def test_concrete_gpt_id_passes_through(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("gpt-4o", s) == "gpt-4o"

    def test_unknown_alias_returns_empty(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        s = self._settings()
        assert resolve_model_alias("unknown-xyz", s) == ""

    def test_none_settings_returns_empty(self):
        from src.infrastructure.providers.model_aliases import resolve_model_alias
        assert resolve_model_alias("haiku", None) == ""


# ---------------------------------------------------------------------------
# 2. 통합: ProfileStore.parse_profile → StrategyBuilder.build → plan.main_model/router_model
# ---------------------------------------------------------------------------


class TestModelWiringIntegration:
    """Profile → Plan 경로에서 main_model/router_model 이 올바르게 흐르는지 검증."""

    def _make_profile(self, main_model: str = "sonnet", router_model: str = "haiku"):
        from src.agent.profile_store import ProfileStore
        # _parse_profile is a staticmethod — call it directly to avoid needing a DB pool.
        return ProfileStore._parse_profile({
            "id": "test-profile",
            "name": "Test Profile",
            "domain_scopes": ["test"],
            "system_prompt": "You are a test assistant.",
            "main_model": main_model,
            "router_model": router_model,
        })

    def test_profile_carries_main_model(self):
        profile = self._make_profile(main_model="opus")
        assert profile.main_model == "opus"

    def test_profile_carries_router_model(self):
        profile = self._make_profile(router_model="sonnet")
        assert profile.router_model == "sonnet"

    def test_plan_main_model_from_strategy_builder(self):
        """StrategyBuilder.build 가 profile.main_model 을 plan 에 전달한다."""
        from src.router.strategy_builder import StrategyBuilder
        from src.domain.execution_plan import QuestionType
        from src.domain.models import AgentMode

        profile = self._make_profile(main_model="opus", router_model="sonnet")
        builder = StrategyBuilder()
        strategy = builder.get_strategy(QuestionType.STANDALONE)
        plan = builder.build(
            profile=profile,
            question_type=QuestionType.STANDALONE,
            strategy=strategy,
            mode=AgentMode.AGENTIC,
            tools=[],
            query="테스트 질문",
        )
        assert plan.main_model == "opus"

    def test_plan_router_model_from_strategy_builder(self):
        """StrategyBuilder.build 가 profile.router_model 을 plan 에 전달한다."""
        from src.router.strategy_builder import StrategyBuilder
        from src.domain.execution_plan import QuestionType
        from src.domain.models import AgentMode

        profile = self._make_profile(main_model="sonnet", router_model="haiku")
        builder = StrategyBuilder()
        strategy = builder.get_strategy(QuestionType.STANDALONE)
        plan = builder.build(
            profile=profile,
            question_type=QuestionType.STANDALONE,
            strategy=strategy,
            mode=AgentMode.AGENTIC,
            tools=[],
            query="테스트 질문",
        )
        assert plan.router_model == "haiku"

    def test_plan_empty_model_when_profile_unset(self):
        """profile.main_model 이 기본 "sonnet" 일 때 plan 에 "sonnet" 이 들어온다."""
        from src.router.strategy_builder import StrategyBuilder
        from src.domain.execution_plan import QuestionType
        from src.domain.models import AgentMode

        profile = self._make_profile(main_model="sonnet")
        builder = StrategyBuilder()
        strategy = builder.get_strategy(QuestionType.STANDALONE)
        plan = builder.build(
            profile=profile,
            question_type=QuestionType.STANDALONE,
            strategy=strategy,
            mode=AgentMode.AGENTIC,
            tools=[],
        )
        assert plan.main_model == "sonnet"


# ---------------------------------------------------------------------------
# 3. GraphExecutor 오버라이드: stub ProviderFactory 주입
# ---------------------------------------------------------------------------


def _make_stub_provider_factory(resolved_model: str = "claude-sonnet-4-5"):
    """get_chat_model 호출을 기록하는 stub ProviderFactory."""
    factory = MagicMock()
    stub_chat_model = MagicMock()
    factory.get_chat_model.return_value = stub_chat_model
    factory._resolved_model = resolved_model
    factory._stub_chat_model = stub_chat_model
    return factory


def _make_executor_with_override(
    settings=None,
    provider_factory=None,
    default_chat_model=None,
):
    """오버라이드 인자를 받는 GraphExecutor 인스턴스 생성."""
    from src.agent.graph_executor import GraphExecutor

    mock_llm = AsyncMock()
    mock_registry = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_registry.get.return_value = mock_tool

    return GraphExecutor(
        main_llm=mock_llm,
        tool_registry=mock_registry,
        guardrails={},
        chat_model=default_chat_model or MagicMock(),
        provider_factory=provider_factory,
        settings=settings,
    )


def _agentic_plan_with_model(main_model: str = "sonnet"):
    from src.domain.models import AgentMode, SearchScope
    from src.domain.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall
    return ExecutionPlan(
        mode=AgentMode.AGENTIC,
        scope=SearchScope(),
        question_type=QuestionType.STANDALONE,
        strategy=QuestionStrategy(needs_rag=True),
        tool_groups=[[ToolCall("test_tool", {})]],
        system_prompt="You are a test assistant.",
        main_model=main_model,
    )


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
@patch("src.agent.graph_executor.resolve_model_alias")
async def test_executor_override_calls_factory_with_resolved_model(
    mock_resolve, mock_convert, mock_build,
):
    """plan.main_model 이 지정됐을 때 stub factory.get_chat_model 이 resolved_name 으로 호출된다."""
    # Arrange
    mock_resolve.return_value = "claude-sonnet-4-5"

    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    from src.config import Settings
    settings = Settings(provider_mode="anthropic", anthropic_api_key="sk-test")
    factory = _make_stub_provider_factory()
    factory.get_chat_model.return_value = MagicMock()  # override chat model

    executor = _make_executor_with_override(settings=settings, provider_factory=factory)
    plan = _agentic_plan_with_model(main_model="sonnet")

    # Act
    await executor.execute("안녕", plan, "sess-1")

    # Assert: stub factory.get_chat_model 이 resolved model_name 으로 호출됐음
    factory.get_chat_model.assert_called_once_with(model_name="claude-sonnet-4-5")
    # build_agentic_graph 가 override chat model로 호출됐음
    call_kwargs = mock_build.call_args
    assert call_kwargs is not None


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
@patch("src.agent.graph_executor.resolve_model_alias")
async def test_executor_override_bypasses_cache(
    mock_resolve, mock_convert, mock_build,
):
    """오버라이드 경로는 공유 graph cache 를 거치지 않아야 한다 (같은 plan 두 번 = build 2회)."""
    mock_resolve.return_value = "claude-sonnet-4-5"

    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    from src.config import Settings
    settings = Settings(provider_mode="anthropic", anthropic_api_key="sk-test")
    factory = _make_stub_provider_factory()
    factory.get_chat_model.return_value = MagicMock()

    executor = _make_executor_with_override(settings=settings, provider_factory=factory)
    plan = _agentic_plan_with_model(main_model="sonnet")

    await executor.execute("질문1", plan, "sess-1")
    await executor.execute("질문2", plan, "sess-2")

    # 캐시를 거치지 않으므로 build 가 2회 호출
    assert mock_build.call_count == 2


# ---------------------------------------------------------------------------
# 4. 회귀 테스트: 기존 경로가 영향받지 않음
# ---------------------------------------------------------------------------


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_regression_empty_main_model_uses_default_chat_model(
    mock_convert, mock_build,
):
    """plan.main_model == "" 이면 stub factory.get_chat_model 이 오버라이드로 호출되지 않는다."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    from src.config import Settings
    settings = Settings(provider_mode="anthropic", anthropic_api_key="sk-test")
    factory = MagicMock()

    default_chat = MagicMock()
    executor = _make_executor_with_override(
        settings=settings, provider_factory=factory, default_chat_model=default_chat,
    )
    # main_model 비어 있음
    plan = _agentic_plan_with_model(main_model="")

    await executor.execute("안녕", plan, "sess-1")

    # factory.get_chat_model 이 오버라이드 목적으로 호출되지 않았음
    factory.get_chat_model.assert_not_called()
    # 기본 self._chat_model(default_chat)을 사용하는 build_agentic_graph 가 호출됨
    mock_build.assert_called_once()
    call_kwargs = mock_build.call_args
    assert call_kwargs.kwargs.get("chat_model") is default_chat or call_kwargs.args[0] is default_chat


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_regression_no_provider_factory_uses_default_path(
    mock_convert, mock_build,
):
    """provider_factory=None 이면 오버라이드 없이 기존 경로를 사용한다."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    from src.config import Settings
    settings = Settings(provider_mode="anthropic", anthropic_api_key="sk-test")

    executor = _make_executor_with_override(
        settings=settings,
        provider_factory=None,  # None 주입
    )
    plan = _agentic_plan_with_model(main_model="sonnet")

    await executor.execute("안녕", plan, "sess-1")

    # build 는 1회 호출되지만 default chat_model 경로로 진행
    mock_build.assert_called_once()


@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_regression_no_settings_uses_default_path(
    mock_convert, mock_build,
):
    """settings=None 이면 오버라이드 없이 기존 경로를 사용한다."""
    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    factory = MagicMock()
    executor = _make_executor_with_override(
        settings=None,  # None 주입
        provider_factory=factory,
    )
    plan = _agentic_plan_with_model(main_model="sonnet")

    await executor.execute("안녕", plan, "sess-1")

    # factory.get_chat_model 이 오버라이드로 호출되지 않음
    factory.get_chat_model.assert_not_called()
    mock_build.assert_called_once()


@patch("src.agent.graph_executor.resolve_model_alias")
@patch("src.agent.graph_executor.build_agentic_graph")
@patch("src.agent.graph_executor.convert_tools_to_langchain")
async def test_regression_unresolvable_alias_uses_default_path(
    mock_convert, mock_build, mock_resolve,
):
    """resolve_model_alias 가 "" 을 반환하면 기존 self._chat_model 경로를 사용한다."""
    mock_resolve.return_value = ""  # 해석 불가 → 폴백

    mock_lc_tool = MagicMock()
    mock_lc_tool.name = "test_tool"
    mock_convert.return_value = [mock_lc_tool]

    mock_app = AsyncMock()
    mock_app.ainvoke = AsyncMock(return_value={
        "messages": [MagicMock(type="ai", content="answer", tool_calls=None)],
    })
    mock_build.return_value = mock_app

    from src.config import Settings
    settings = Settings(provider_mode="anthropic", anthropic_api_key="sk-test")
    factory = MagicMock()
    default_chat = MagicMock()

    executor = _make_executor_with_override(
        settings=settings, provider_factory=factory, default_chat_model=default_chat,
    )
    plan = _agentic_plan_with_model(main_model="unknown-alias")

    await executor.execute("안녕", plan, "sess-1")

    # factory.get_chat_model 이 호출되지 않음
    factory.get_chat_model.assert_not_called()
    mock_build.assert_called_once()
