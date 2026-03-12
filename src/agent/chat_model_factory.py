"""ChatModel 팩토리.

ProviderFactory 설정을 재활용하여 LangChain ChatModel을 생성한다.
결정론적 모드는 기존 LLMProvider를 사용하므로 이 모듈은 에이전틱 모드 전용.
"""

from langchain_core.language_models import BaseChatModel

from src.config import ProviderMode


def create_chat_model(
    provider_mode: ProviderMode,
    model_name: str = "",
    ollama_host: str = "http://localhost:11434",
    openai_api_key: str = "",
    server_url: str = "",
) -> BaseChatModel:
    """설정 기반 ChatModel 생성.

    Args:
        provider_mode: development/openai/production
        model_name: 모델명
        ollama_host: Ollama 서버 주소
        openai_api_key: OpenAI API 키
        server_url: GPU/MLX 서버 URL (OpenAI 호환)

    Returns:
        BaseChatModel (tool calling 지원)
    """
    # GPU/MLX 서버가 설정되면 OpenAI 호환 API로 연결
    if server_url:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            base_url=f"{server_url.rstrip('/')}/v1",
            api_key="not-needed",
            model=model_name or "default",
        )

    if provider_mode == ProviderMode.DEVELOPMENT:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name,
            base_url=ollama_host,
        )

    # openai / production
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_name,
        api_key=openai_api_key,
    )
