"""AIP_MAIN_LLM_BACKEND — main만 상용 스왑하는 선택적 오버라이드."""

from unittest.mock import MagicMock, patch

from src.infrastructure.providers.factory import ProviderFactory


def _settings(**over):
    s = MagicMock()
    s.provider_mode = "development"
    s.main_llm_server_url = "http://localhost:8106"
    s.router_llm_server_url = "http://localhost:8105"
    s.orchestrator_server_url = ""
    s.main_model = "qwen"
    s.router_model = "qwen-small"
    s.orchestrator_model = "qwen-mid"
    s.anthropic_main_model = "claude-haiku-4-5"
    s.anthropic_router_model = "claude-haiku-4-5"
    s.anthropic_api_key = "sk-test"
    s.llm_max_tokens = 4096
    s.main_llm_backend = ""
    for k, v in over.items():
        setattr(s, k, v)
    return s


def test_main_override_anthropic_keeps_router_local():
    """main만 anthropic — 라우터는 로컬(http) 유지 (로컬 LLM 원칙)."""
    factory = ProviderFactory(_settings(main_llm_backend="anthropic"))
    with patch("src.infrastructure.providers.llm.anthropic.AnthropicLLMProvider") as anth, \
         patch("src.infrastructure.providers.llm.http_llm.HttpLLMProvider") as http:
        factory.get_main_llm()
        anth.assert_called_once()
        factory.get_router_llm()
        http.assert_called_once()


def test_no_override_follows_provider_mode():
    factory = ProviderFactory(_settings())
    with patch("src.infrastructure.providers.llm.http_llm.HttpLLMProvider") as http:
        factory.get_main_llm()
        http.assert_called_once()
