"""프로바이더 팩토리.

모드에 따라 적절한 구현체를 생성한다.
HTTP 서버 URL이 설정되면 GPU 서버를 우선 사용.
"""

import logging

import httpx

from src.config import ProviderMode, Settings
from src.locale.bundle import get_locale

from .base import EmbeddingProvider, LLMProvider, ParsingProvider, RerankerProvider

logger = logging.getLogger(__name__)


class ProviderFactory:
    """프로바이더 모드에 따라 적절한 구현체를 생성한다."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._mode = settings.provider_mode

    @property
    def _is_local(self) -> bool:
        return self._mode == ProviderMode.DEVELOPMENT

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

        if self._is_local:
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

    def get_router_llm(self) -> LLMProvider:
        return self._create_llm(
            server_url=self._settings.router_llm_server_url,
            local_model=self._settings.router_model,
            label="router",
        )

    def get_main_llm(self) -> LLMProvider:
        return self._create_llm(
            server_url=self._settings.main_llm_server_url,
            local_model=self._settings.main_model,
            label="main",
        )

    def _create_llm(self, server_url: str, local_model: str, label: str) -> LLMProvider:
        """LLM 프로바이더 생성 (router/main 공통 로직)."""
        system_prefix = get_locale().prompt("llm_system_prefix")

        if server_url:
            from .llm.http_llm import HttpLLMProvider

            logger.info("Using HTTP LLM server (%s): %s", label, server_url)
            return HttpLLMProvider(
                base_url=server_url,
                system_prefix=system_prefix,
                max_tokens=self._settings.llm_max_tokens,
            )

        if self._is_local:
            from .llm.ollama import OllamaProvider

            return OllamaProvider(
                base_url=self._settings.ollama_host,
                model=local_model,
                num_ctx=self._settings.ollama_num_ctx,
                system_prefix=system_prefix,
            )

        from .llm.openai import OpenAILLMProvider

        return OpenAILLMProvider(
            api_key=self._settings.openai_api_key,
            model=self._settings.prod_llm_model,
            system_prefix=system_prefix,
            max_tokens=self._settings.llm_max_tokens,
        )

    def get_parsing_provider(self) -> ParsingProvider:
        parser_type = self._settings.parser_provider.lower()

        if parser_type == "engine":
            from src.pipeline.parsing.engine import ParsingEngine
            from src.pipeline.parsing.provider_adapter import ParsingEngineProvider

            engine = ParsingEngine(
                enable_docling=self._settings.parser_enable_docling,
                enable_vlm=self._settings.parser_enable_vlm,
                vlm_endpoint=self._settings.vlm_ocr_endpoint,
                csv_max_rows=self._settings.parser_csv_max_rows,
                excel_max_rows_per_sheet=self._settings.parser_excel_max_rows,
            )
            logger.info(
                "Using unified parsing engine (docling=%s, vlm=%s)",
                self._settings.parser_enable_docling,
                self._settings.parser_enable_vlm,
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
        """서버 헬스체크. startup 시점에만 호출되므로 동기 허용."""
        try:
            r = httpx.get(f"{base_url.rstrip('/')}/health", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False
