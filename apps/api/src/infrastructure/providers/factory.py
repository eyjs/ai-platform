"""프로바이더 팩토리.

모드에 따라 적절한 구현체를 생성한다.
HTTP 서버 URL이 설정되면 GPU 서버를 우선 사용.
"""

import logging
from typing import Callable

import httpx

from src.config import ProviderMode, Settings
from src.locale.bundle import get_locale

from .base import (
    EmbeddingProvider,
    LLMProvider,
    ParsingProvider,
    RerankerProvider,
)
from .registry import ProviderRegistry

logger = logging.getLogger(__name__)

# 무료 코어스 콘텐츠(운세·타로 등) 생성 상한. primary(DGX)·fallback(MLX) 양쪽에 같은
# 값을 걸어야 경로에 따라 길이가 달라지지 않는다.
_FREE_CONTENT_MAX_TOKENS = 1024


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

    # === LLM 백엔드 선택 규칙 (provider_mode) ===
    # ★DGX 위임(2026-07-16) 이후 이 메서드는 더 이상 마스터 스위치가 아니다.
    # dgx_llm_url이 설정되면 primary는 DGX로 고정되고, 이 메서드는 **로컬 폴백 base를
    # 만들 때만** 호출된다(_wrap_with_dgx 참조). 즉 provider_mode를 anthropic으로 바꿔도
    # 최종 답변은 여전히 DGX가 낸다 — 폴백이 Claude가 될 뿐이다.
    # 그 오해를 부팅 때 잡으려고 _check_llm_wiring_alignment(bootstrap)가 WARN을 띄운다.
    def _llm_backend(self, server_url: str) -> str:
        """provider_mode 기준 LLM 백엔드를 고른다 (DGX 미설정 시 primary, 설정 시 폴백).

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
        return self._wrap_with_dgx(
            lambda: self._create_llm(
                server_url=self._settings.router_llm_server_url,
                local_model=self._settings.router_model,
                anthropic_model=self._settings.anthropic_router_model,
                label="router",
            ),
            "router", self._dgx_model_for("router"),
        )

    def get_orchestration_llm(self) -> LLMProvider:
        """오케스트레이션(계획수립·쿼리재작성·확장)용 LLM.

        투트랙 라이트사이징:
          - 순수 분류(의도/모드)는 router_llm(1.7B 등 초경량),
          - 생성적 오케스트레이션(계획·재작성·확장)은 이 provider(4B 등 중경량),
          - 생성은 main_llm(9B+).
        orchestrator_server_url 미설정 시 router_llm_server_url 로 폴백(하위호환).

        DGX 설정 시 라이트사이징은 폴백 경로에만 남는다 — 원격은 MoE 단일 모델이
        분류든 생성이든 활성 파라미터가 같아 모델을 쪼갤 이득이 없다(_dgx_model_for 참조).
        """
        s = self._settings
        return self._wrap_with_dgx(
            lambda: self._create_llm(
                server_url=s.orchestrator_server_url or s.router_llm_server_url,
                local_model=s.orchestrator_model,
                anthropic_model=s.anthropic_router_model,
                label="orchestration",
            ),
            "orchestration", self._dgx_model_for("orchestration"),
        )

    def _dgx_model_for(self, role: str) -> str:
        """역할별 DGX 모델. 미지정이면 dgx_main_model — 즉 전 역할이 한 모델을 공유한다.

        한 모델로 몰아야 하는 이유 두 가지:
        1. ollama는 동시 상주 모델 수가 제한(기본 3)이라 역할마다 다른 모델을 주면
           evict↔reload가 돌며 매번 콜드로드를 문다(실측: gpt-oss:120b 로드 143초,
           그 과정에서 qwen3.6:35b-a3b가 쫓겨남).
        2. dgx_main_model(qwen3.6:35b-a3b)은 MoE라 활성 파라미터가 3B뿐이다. 분류처럼
           가벼운 일도 7B급과 지연이 같아(실측 0.66s vs 0.59s) 라이트사이징 실익이 없다.
        """
        s = self._settings
        override = {
            "report": s.dgx_report_model,
            "router": s.dgx_router_model,
            "orchestration": s.dgx_orchestrator_model,
            "fortune": s.dgx_fortune_model,
        }.get(role, "")
        return override or s.dgx_main_model

    def _wrap_with_dgx(
        self, base_factory: Callable[[], LLMProvider], label: str, model: str,
        max_tokens: int | None = None,
    ) -> LLMProvider:
        """DGX Spark(원격 GPU)를 primary로, base(현행 로컬/상용)를 fallback으로 감싼다.

        dgx_llm_url 미설정이면 base를 그대로 반환 — DGX 없는 환경(CI·타 개발자)은 무영향.

        dgx_local_fallback=False(기본)면 폴백 없이 DGX 단독 provider를 돌려준다 —
        로컬 MLX를 걷어낸 구성. True면 base를 폴백으로 붙인다(사용자 요구였던
        "DGX 연결이 끊어지면 현재 구조로 폴백").

        base를 콜러블로 받는 이유: 폴백을 끈 구성에서 base를 아예 만들지 않기 위해서다.
        미리 만들면 쓰지도 않을 httpx 클라이언트가 뜨고, 무엇보다 "Using LOCAL MLX LLM"
        같은 로그가 남아 실제 배선을 오독하게 된다.

        max_tokens는 base와 같은 상한을 primary에도 걸기 위한 것 — 안 넘기면 원격만
        무제한 생성이 된다(무료 콘텐츠 등 상한이 의미 있는 경로에서 필수).
        """
        if not self._settings.dgx_llm_url:
            return base_factory()
        from .llm.failover import FailoverLLMProvider
        from .llm.ollama import OllamaProvider

        primary = OllamaProvider(
            base_url=self._settings.dgx_llm_url,
            model=model,
            num_ctx=self._settings.ollama_num_ctx,
            system_prefix=get_locale().prompt("llm_system_prefix"),
            # 원격 DGX가 다운되면 짧은 connect 타임아웃으로 즉시 감지 → 로컬 폴백.
            # read는 무제한(None) — 복잡한 쿼리 생성이 수 분~수십 분 걸려도 자르지
            # 않는다(부하 큰 generation 위임이 이 경로의 목적). connect만 짧게 잡아
            # "다운은 즉시 폴백, 생성은 끝까지 대기"를 동시에 만족.
            connect_timeout=3.0,
            read_timeout=None,
            max_tokens=max_tokens,
        )
        if not self._settings.dgx_local_fallback:
            logger.info(
                "Using DGX Spark ollama (%s, DGX 단독): %s @ %s — 로컬 폴백 없음",
                label, model, self._settings.dgx_llm_url,
            )
            return primary
        logger.info(
            "Using DGX Spark ollama (%s, primary): %s @ %s — fallback: 현행 로컬",
            label, model, self._settings.dgx_llm_url,
        )
        return FailoverLLMProvider(primary, base_factory(), label=label)

    def get_main_llm(self) -> LLMProvider:
        return self._wrap_with_dgx(
            lambda: self._create_llm(
                server_url=self._settings.main_llm_server_url,
                local_model=self._settings.main_model,
                anthropic_model=self._settings.anthropic_main_model,
                label="main",
                backend_override=self._settings.main_llm_backend,
            ),
            "main", self._dgx_model_for("main"),
        )

    # === 하이브리드 라우팅용 명시 provider (provider_mode 무시) ===
    # 무료/패턴 콘텐츠($0)는 로컬, 고난도 추론은 상용 — 요청별 선택용.

    def get_local_llm(self) -> LLMProvider:
        """무료·패턴 콘텐츠용 로컬 MLX LLM($0, GPU 가속). MLX 전용 — ollama 사용 안 함."""
        from .llm.http_llm import HttpLLMProvider

        system_prefix = get_locale().prompt("llm_system_prefix")
        # 무료 콘텐츠 전용 MLX(8106=9B). main_llm_server_url과 분리 — 챗 모델 자동감지 오염·
        # kms 8104(14B) 폴백 금지. 미설정 시 8106 기본(전용서버), 8104로 폴백하지 않음.
        url = self._settings.fortune_llm_server_url or "http://host.docker.internal:8106"

        def _local() -> LLMProvider:
            # 무료 코어스 콘텐츠는 간결 → max_tokens 제한(9B 생성시간 bound, 타임아웃 방지).
            logger.info("Using LOCAL MLX LLM (GPU): %s", url)
            return HttpLLMProvider(
                base_url=url, system_prefix=system_prefix,
                max_tokens=_FREE_CONTENT_MAX_TOKENS,
            )

        # DGX 우선. 같은 max_tokens를 원격에도 걸어 경로별 길이 차이를 없앤다.
        return self._wrap_with_dgx(
            _local, "fortune", self._dgx_model_for("fortune"),
            max_tokens=_FREE_CONTENT_MAX_TOKENS,
        )

    def get_report_llm(self) -> LLMProvider:
        """사주 리포트 전용 LLM. DGX Spark 우선 + 로컬 MLX 자동 폴백.

        report_llm_server_url이 설정되면 그 MLX 서버(14B 등)가 폴백이 되고,
        미설정 시 get_main_llm()에 위임한다(그쪽도 DGX 우선 + 9B 폴백).

        폴백을 main(9B)이 아닌 14B로 두는 이유: 리포트는 JSON 안정성이 중요한데
        9B는 리포트 JSON에서 반복붕괴 위험이 있다. DGX 단절 시에도 품질을 지킨다.
        """
        url = self._settings.report_llm_server_url
        if not url:
            return self.get_main_llm()
        from .llm.http_llm import HttpLLMProvider

        def _local() -> LLMProvider:
            logger.info("Using REPORT LLM server: %s", url)
            return HttpLLMProvider(
                base_url=url, system_prefix=get_locale().prompt("llm_system_prefix"),
                max_tokens=self._settings.llm_max_tokens,
            )

        return self._wrap_with_dgx(
            _local, "report", self._dgx_model_for("report"),
            max_tokens=self._settings.llm_max_tokens,
        )

    def get_commercial_llm(self) -> LLMProvider:
        """고난도 추론용 상용 LLM(Anthropic Haiku). 키 없으면 로컬 폴백. mode 무시."""
        if not self._settings.anthropic_api_key:
            logger.warning("anthropic_api_key 미설정 — commercial 요청이 로컬 LLM으로 폴백(품질 저하 가능)")
            return self.get_local_llm()
        from .llm.anthropic import AnthropicLLMProvider

        system_prefix = get_locale().prompt("llm_system_prefix")
        logger.info("Using COMMERCIAL Anthropic: %s", self._settings.anthropic_main_model)
        return AnthropicLLMProvider(
            api_key=self._settings.anthropic_api_key, model=self._settings.anthropic_main_model,
            system_prefix=system_prefix, max_tokens=self._settings.llm_max_tokens,
        )

    def _create_llm(
        self, server_url: str, local_model: str, label: str,
        anthropic_model: str = "claude-haiku-4-5",
        backend_override: str = "",
    ) -> LLMProvider:
        """결정론 경로용 LLMProvider 생성. 백엔드는 _llm_backend가 단일 결정.

        backend_override가 지정되면 provider_mode보다 우선한다 —
        main만 상용으로 스왑하는 선택적 하이브리드용(AIP_MAIN_LLM_BACKEND).
        """
        system_prefix = get_locale().prompt("llm_system_prefix")
        backend = backend_override or self._llm_backend(server_url)
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

        DGX 설정 시 ollama의 OpenAI 호환 엔드포인트(/v1)를 ChatOpenAI로 쓴다.
        langchain_ollama(ChatOllama)를 안 쓰는 이유: 이 이미지에 미설치이고, /v1로도
        tool calling이 정상 동작한다(qwen3.6:35b-a3b capabilities에 tools 포함).
        model_name(로컬 MLX 자동감지 결과)은 DGX 경로에서 무시한다 — 그건 MLX 모델명이라
        DGX에 존재하지 않는다.

        ★reasoning_effort="none" 필수: qwen3.6은 thinking 모델이라 기본적으로 추론을
        content가 아닌 `reasoning` 필드로 흘린다. 그러면 LangChain이 읽는 delta.content가
        계속 비어 agent_timeout_seconds(30s) 안에 토큰이 0개가 되고 챗이 통째로 죽는다.
        네이티브 API의 think:false는 /v1에서 무시되고(실측), reasoning_effort만 먹는다.

        폴백은 LLMProvider 경로(_wrap_with_dgx)와 기전이 다르다. 여긴 langchain
        Runnable이라 with_fallbacks()를 쓴다 — create_react_agent가 넘겨받아
        bind_tools()를 호출해도 RunnableWithFallbacks가 primary·fallback 양쪽에
        도구를 바인딩해 되감으므로 폴백이 유지된다(실측).
        """
        s = self._settings
        if s.dgx_llm_url:
            from langchain_openai import ChatOpenAI

            dgx_model = self._dgx_model_for("main")
            primary = ChatOpenAI(
                base_url=f"{s.dgx_llm_url.rstrip('/')}/v1",
                api_key="not-needed", model=dgx_model, streaming=True,
                reasoning_effort="none",
                # connect만 짧게, read는 무제한 — OllamaProvider와 같은 이유다(다운은
                # 즉시 감지, 긴 생성은 끝까지 대기). max_retries=0: openai 클라이언트
                # 기본 2회 재시도가 붙으면 DGX 다운 시 폴백이 그만큼 늦어진다.
                timeout=httpx.Timeout(None, connect=3.0),
                max_retries=0,
            )
            if not s.dgx_local_fallback:
                logger.info(
                    "Using DGX Spark chat model (agentic, DGX 단독): %s @ %s/v1 — 로컬 폴백 없음",
                    dgx_model, s.dgx_llm_url,
                )
                return primary
            logger.info(
                "Using DGX Spark chat model (agentic, primary): %s @ %s/v1 — fallback: 현행 로컬",
                dgx_model, s.dgx_llm_url,
            )
            return primary.with_fallbacks([self._local_chat_model(model_name)])

        return self._local_chat_model(model_name)

    def _local_chat_model(self, model_name: str = ""):
        """DGX를 뺀 현행(로컬 MLX/ollama/상용) langchain BaseChatModel.

        DGX 경로의 폴백으로도 쓰이므로 단독 호출 가능해야 한다.
        """
        s = self._settings
        backend = self._llm_backend(s.main_llm_server_url)

        if backend == "http":
            from langchain_openai import ChatOpenAI

            # streaming=True: agentic astream_events가 on_chat_model_stream을 토큰 단위로
            # 발생시키도록(MLX 서버는 stream=True를 지원). 미설정 시 최종 답변이 한 청크로 와
            # 프론트에서 한번에 렌더링됨.
            return ChatOpenAI(
                base_url=f"{s.main_llm_server_url.rstrip('/')}/v1",
                api_key="not-needed", model=model_name or s.main_model or "default",
                streaming=True,
            )

        if backend == "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(model=model_name or s.main_model, base_url=s.ollama_host, streaming=True)

        if backend == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=model_name or s.anthropic_main_model,
                api_key=s.anthropic_api_key, max_tokens=s.llm_max_tokens,
            )

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name or s.prod_llm_model, api_key=s.openai_api_key)

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
