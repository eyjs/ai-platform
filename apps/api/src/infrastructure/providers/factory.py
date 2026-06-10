"""프로바이더 팩토리.

모드에 따라 적절한 구현체를 생성한다.
HTTP 서버 URL이 설정되면 GPU 서버를 우선 사용.
"""

import logging

import httpx

from src.config import ProviderMode, Settings
from src.locale.bundle import get_locale

from .base import (
    EmbeddingProvider,
    LLMProvider,
    OrchestratorLLMConfig,
    ParsingProvider,
    RerankerProvider,
)
from .registry import ProviderRegistry

logger = logging.getLogger(__name__)


class ProviderFactory:
    """프로바이더 모드에 따라 적절한 구현체를 생성한다."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._mode = settings.provider_mode

    @property
    def _is_local(self) -> bool:
        return self._mode == ProviderMode.DEVELOPMENT

    @property
    def _aux_prefers_local(self) -> bool:
        """임베딩/리랭커 등 보조 모델을 로컬로 둘지.

        Anthropic은 자체 임베딩이 없으므로, anthropic 모드에서 OpenAI 키도
        임베딩 서버도 없으면 로컬(sentence-transformers/cross-encoder)로 폴백한다.
        """
        if self._is_local:
            return True
        return (
            self._mode == ProviderMode.ANTHROPIC
            and not self._settings.openai_api_key
        )

    def get_embedding_provider(self) -> EmbeddingProvider:
        if self._settings.embedding_server_url:
            from .embedding.http_embedding import HttpEmbeddingProvider

            logger.info("Using HTTP embedding server: %s", self._settings.embedding_server_url)
            return HttpEmbeddingProvider(
                base_url=self._settings.embedding_server_url,
                max_concurrent=self._settings.embedding_concurrent_requests,
                timeout=self._settings.embedding_timeout,
                connect_timeout=self._settings.embedding_connect_timeout,
            )

        if self._aux_prefers_local:
            from .embedding.sentence_transformers import SentenceTransformersProvider

            logger.info("Using local embedding model (CPU)")
            return SentenceTransformersProvider(
                model_name=self._settings.dev_embedding_model,
            )

        from .embedding.openai import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=self._settings.openai_api_key,
            model=self._settings.prod_embedding_model,
        )

    # === LLM 백엔드 단일 선택 규칙 (provider_mode = 마스터 스위치) ===
    # 모든 LLM 소비자(결정론 LLMProvider / 에이전틱 ChatModel / 오케스트레이터)가
    # 이 한 메서드로 백엔드를 결정한다. provider_mode를 바꾸면 전부 따라간다.
    def _llm_backend(self, server_url: str) -> str:
        """provider_mode 기준 LLM 백엔드를 고른다.

        - anthropic: 항상 Claude (server_url 무시 — 모드가 최우선)
        - development: MLX(server_url) 있으면 http, 없으면 ollama
        - openai/production: openai
        """
        if self._mode == ProviderMode.ANTHROPIC:
            return "anthropic"
        if self._mode == ProviderMode.DEVELOPMENT:
            return "http" if server_url else "ollama"
        return "openai"

    def get_router_llm(self) -> LLMProvider:
        return self._create_llm(
            server_url=self._settings.router_llm_server_url,
            local_model=self._settings.router_model,
            anthropic_model=self._settings.anthropic_router_model,
            label="router",
        )

    def get_main_llm(self) -> LLMProvider:
        return self._create_llm(
            server_url=self._settings.main_llm_server_url,
            local_model=self._settings.main_model,
            anthropic_model=self._settings.anthropic_main_model,
            label="main",
        )

    def _create_llm(
        self, server_url: str, local_model: str, label: str,
        anthropic_model: str = "claude-haiku-4-5",
    ) -> LLMProvider:
        """결정론 경로용 LLMProvider 생성. 백엔드는 _llm_backend가 단일 결정."""
        system_prefix = get_locale().prompt("llm_system_prefix")
        backend = self._llm_backend(server_url)
        max_tokens = self._settings.llm_max_tokens

        if backend == "http":
            from .llm.http_llm import HttpLLMProvider

            logger.info("Using HTTP LLM server (%s): %s", label, server_url)
            return HttpLLMProvider(base_url=server_url, system_prefix=system_prefix, max_tokens=max_tokens)

        if backend == "ollama":
            from .llm.ollama import OllamaProvider

            return OllamaProvider(
                base_url=self._settings.ollama_host, model=local_model,
                num_ctx=self._settings.ollama_num_ctx, system_prefix=system_prefix,
            )

        if backend == "anthropic":
            from .llm.anthropic import AnthropicLLMProvider

            logger.info("Using Anthropic Claude (%s): %s", label, anthropic_model)
            return AnthropicLLMProvider(
                api_key=self._settings.anthropic_api_key, model=anthropic_model,
                system_prefix=system_prefix, max_tokens=max_tokens,
            )

        from .llm.openai import OpenAILLMProvider

        return OpenAILLMProvider(
            api_key=self._settings.openai_api_key, model=self._settings.prod_llm_model,
            system_prefix=system_prefix, max_tokens=max_tokens,
        )

    def get_chat_model(self, model_name: str = ""):
        """에이전틱(LangGraph)용 langchain BaseChatModel. 백엔드는 _llm_backend가 단일 결정.

        model_name: MLX 자동감지 모델명 override (없으면 모드별 기본 모델).
        ImportError(langchain extra 미설치)는 호출부(bootstrap)에서 흡수 → agentic만 비활성.
        """
        s = self._settings
        backend = self._llm_backend(s.main_llm_server_url)

        if backend == "http":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                base_url=f"{s.main_llm_server_url.rstrip('/')}/v1",
                api_key="not-needed", model=model_name or s.main_model or "default",
            )

        if backend == "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(model=model_name or s.main_model, base_url=s.ollama_host)

        if backend == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model_name or s.anthropic_main_model,
                api_key=s.anthropic_api_key, max_tokens=s.llm_max_tokens,
            )

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name or s.prod_llm_model, api_key=s.openai_api_key)

    def get_orchestrator_llm_config(self) -> "OrchestratorLLMConfig | None":
        """오케스트레이터(프로필 선택)용 LLM 백엔드 설정. provider_mode가 백엔드를 결정.

        어댑터 객체를 직접 만들지 않고 설정만 반환한다 — 생성은 bootstrap이
        맡아 infrastructure → orchestrator 역방향 의존을 피한다.

        Returns: OrchestratorLLMConfig 또는 None(비활성/키 없음).
        """
        s = self._settings
        if not s.orchestrator_enabled:
            return None

        if self._mode == ProviderMode.ANTHROPIC:
            provider, model, api_key, server_url = (
                "anthropic", s.anthropic_router_model, s.anthropic_api_key, "",
            )
        elif self._mode == ProviderMode.DEVELOPMENT:
            provider = s.orchestrator_provider  # mlx | ollama
            model = s.orchestrator_model
            api_key = ""
            server_url = s.orchestrator_server_url or s.router_llm_server_url
        else:  # openai / production
            provider, model, api_key, server_url = (
                "openai", s.orchestrator_model, s.openai_api_key, "",
            )

        if provider in ("openai", "anthropic") and not api_key:
            logger.warning("orchestrator_disabled: %s 키 없음", provider)
            return None

        return OrchestratorLLMConfig(
            provider=provider, model=model, api_key=api_key,
            timeout=s.orchestrator_timeout, server_url=server_url, ollama_host=s.ollama_host,
        )

    def get_parsing_provider(self) -> ParsingProvider:
        parser_type = self._settings.parser_provider.lower()

        if parser_type == "engine":
            from src.pipeline.parsing.engine import ParsingEngine
            from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

            engine = ParsingEngine(
                docforge_url=self._settings.docforge_url,
                docforge_timeout_sec=self._settings.docforge_timeout_sec,
                docforge_internal_key=self._settings.docforge_internal_key,
                docforge_max_wait_sec=self._settings.docforge_max_wait_sec,
            )
            logger.info(
                "Using DocForge parsing service: %s",
                self._settings.docforge_url,
            )
            return ParsingEngineProvider(engine)

        if parser_type == "llamaparse":
            if not self._settings.llamaparse_api_key:
                raise ValueError("AIP_LLAMAPARSE_API_KEY is required for llamaparse provider")
            from .parsing.llama_parse import LlamaParseProvider

            logger.info("Using LlamaParse vision parser")
            return LlamaParseProvider(
                api_key=self._settings.llamaparse_api_key,
                timeout=self._settings.parser_timeout,
            )

        from .parsing.text import TextParsingProvider

        logger.info("Using text-based parser (fallback)")
        return TextParsingProvider()

    def get_reranker(self) -> RerankerProvider:
        if self._settings.reranker_server_url:
            from .reranking.http_reranker import HttpRerankerProvider

            reachable = self._check_server_health(self._settings.reranker_server_url)
            fallback_model = self._settings.reranker_model if self._aux_prefers_local else None
            if reachable:
                logger.info("Using HTTP reranker server: %s", self._settings.reranker_server_url)
            else:
                logger.warning(
                    "HTTP reranker unreachable: %s (will fallback to local)",
                    self._settings.reranker_server_url,
                )
            return HttpRerankerProvider(
                base_url=self._settings.reranker_server_url,
                fallback_model=fallback_model,
            )

        if self._aux_prefers_local:
            try:
                from .reranking.cross_encoder import CrossEncoderReranker

                logger.info("Using local cross-encoder reranker (CPU)")
                return CrossEncoderReranker(model_name=self._settings.reranker_model)
            except Exception as e:
                logger.warning("Cross-encoder unavailable: %s", e)

        from .reranking.llm_reranker import LLMReranker

        return LLMReranker(llm=self.get_router_llm())

    def build_registry(self) -> ProviderRegistry:
        """활성 LLM Provider 들을 등록한 Registry 반환.

        정책:
        - 기본: main_llm 을 "main" 으로 등록 (하위 호환). provider_id 는 capability 기준.
        - AIP_PROVIDER_ENABLE_ANTHROPIC=1 → AnthropicStubProvider 추가 (stub).
        - 환경변수 `AIP_PROVIDER_ENABLE_OPENAI=1` 이면서 openai_api_key 가 있으면 openai 활성.
        """
        import os

        registry = ProviderRegistry()

        # 주 Provider (기존 config 기반)
        main = self.get_main_llm()
        registry.register_inplace(main)

        # 환경 플래그 기반 추가 Provider
        enable_anthropic = os.getenv("AIP_PROVIDER_ENABLE_ANTHROPIC", "0") == "1"
        if enable_anthropic:
            from .llm.anthropic import AnthropicStubProvider
            registry.register_inplace(AnthropicStubProvider())
            logger.info("Registered anthropic_claude (stub)")

        enable_openai = os.getenv("AIP_PROVIDER_ENABLE_OPENAI", "0") == "1"
        if enable_openai and self._settings.openai_api_key:
            # 이미 main 이 openai 면 중복 등록 방지
            if not registry.has("openai"):
                from .llm.openai import OpenAILLMProvider
                from src.locale.bundle import get_locale
                registry.register_inplace(OpenAILLMProvider(
                    api_key=self._settings.openai_api_key,
                    model=self._settings.prod_llm_model,
                    system_prefix=get_locale().prompt("llm_system_prefix"),
                    max_tokens=self._settings.llm_max_tokens,
                ))
                logger.info("Registered openai")

        logger.info("Provider registry built: ids=%s", registry.ids())
        return registry

    @staticmethod
    def _check_server_health(base_url: str, timeout: float = 3.0) -> bool:
        """서버 헬스체크. startup 시점에만 호출되므로 동기 허용."""
        try:
            r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False
