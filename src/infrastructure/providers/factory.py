"""프로바이더 팩토리.

모드에 따라 적절한 구현체를 생성한다.
HTTP 서버 URL이 설정되면 GPU 서버를 우선 사용.
"""

import logging

from src.config import ProviderMode, Settings

from .base import EmbeddingProvider, LLMProvider, RerankerProvider

logger = logging.getLogger(__name__)

_LLM_SYSTEM_PREFIX = "반드시 한국어로만 답변하세요."


class ProviderFactory:
    """프로바이더 모드에 따라 적절한 구현체를 생성한다."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._mode = settings.provider_mode

    @property
    def _is_local(self) -> bool:
        return self._mode == ProviderMode.DEVELOPMENT

    @property
    def _is_gemini(self) -> bool:
        return self._mode == ProviderMode.GEMINI

    def get_embedding_provider(self) -> EmbeddingProvider:
        if self._settings.embedding_server_url:
            from .embedding.http_embedding import HttpEmbeddingProvider

            logger.info("Using HTTP embedding server: %s", self._settings.embedding_server_url)
            return HttpEmbeddingProvider(
                base_url=self._settings.embedding_server_url,
                max_concurrent=self._settings.embedding_concurrent_requests,
            )

        if self._is_local:
            from .embedding.sentence_transformers import SentenceTransformersProvider

            logger.info("Using local embedding model (CPU)")
            return SentenceTransformersProvider(
                model_name=self._settings.dev_embedding_model,
            )

        if self._is_gemini:
            from .embedding.gemini import GeminiEmbeddingProvider

            return GeminiEmbeddingProvider(
                api_key=self._settings.gemini_api_key,
                model=self._settings.gemini_embedding_model,
            )

        from .embedding.openai import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=self._settings.openai_api_key,
            model=self._settings.prod_embedding_model,
        )

    def get_router_llm(self) -> LLMProvider:
        if self._settings.router_llm_server_url:
            from .llm.http_llm import HttpLLMProvider

            logger.info("Using HTTP LLM server (router): %s", self._settings.router_llm_server_url)
            return HttpLLMProvider(
                base_url=self._settings.router_llm_server_url,
                system_prefix=_LLM_SYSTEM_PREFIX,
            )

        if self._is_local:
            from .llm.ollama import OllamaProvider

            return OllamaProvider(
                base_url=self._settings.ollama_host,
                model=self._settings.router_model,
                num_ctx=self._settings.ollama_num_ctx,
                system_prefix=_LLM_SYSTEM_PREFIX,
            )

        if self._is_gemini:
            from .llm.gemini import GeminiLLMProvider

            return GeminiLLMProvider(
                api_key=self._settings.gemini_api_key,
                model="gemini-2.0-flash",
            )

        from .llm.openai import OpenAILLMProvider

        return OpenAILLMProvider(
            api_key=self._settings.openai_api_key,
            model="gpt-4o-mini",
        )

    def get_main_llm(self) -> LLMProvider:
        if self._settings.main_llm_server_url:
            from .llm.http_llm import HttpLLMProvider

            logger.info("Using HTTP LLM server (main): %s", self._settings.main_llm_server_url)
            return HttpLLMProvider(
                base_url=self._settings.main_llm_server_url,
                system_prefix=_LLM_SYSTEM_PREFIX,
            )

        if self._is_local:
            from .llm.ollama import OllamaProvider

            return OllamaProvider(
                base_url=self._settings.ollama_host,
                model=self._settings.main_model,
                num_ctx=self._settings.ollama_num_ctx,
                system_prefix=_LLM_SYSTEM_PREFIX,
            )

        if self._is_gemini:
            from .llm.gemini import GeminiLLMProvider

            return GeminiLLMProvider(
                api_key=self._settings.gemini_api_key,
                model=self._settings.gemini_llm_model,
            )

        from .llm.openai import OpenAILLMProvider

        return OpenAILLMProvider(
            api_key=self._settings.openai_api_key,
            model=self._settings.prod_llm_model,
        )

    def get_reranker(self) -> RerankerProvider:
        if self._settings.reranker_server_url:
            from .reranking.http_reranker import HttpRerankerProvider

            reachable = self._check_server_health(self._settings.reranker_server_url)
            fallback_model = self._settings.reranker_model if self._is_local else None
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

        if self._is_local:
            try:
                from .reranking.cross_encoder import CrossEncoderReranker

                logger.info("Using local cross-encoder reranker (CPU)")
                return CrossEncoderReranker(model_name=self._settings.reranker_model)
            except Exception as e:
                logger.warning("Cross-encoder unavailable: %s", e)

        from .reranking.llm_reranker import LLMReranker

        return LLMReranker(llm=self.get_router_llm())

    @staticmethod
    def _check_server_health(base_url: str, timeout: float = 3.0) -> bool:
        import httpx

        try:
            r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False
