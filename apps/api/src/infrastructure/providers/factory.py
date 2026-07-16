"""프로바이더 팩토리.

LLM primary 는 DGX Spark(ollama) 하나이고, 폴백은 로컬 MLX/ollama 다.
상용(Anthropic/OpenAI) 경로는 2026-07-16 에 제거됐다 — 서빙 경로에 벤더는 없다.
HTTP 서버 URL이 설정되면 GPU 서버를 우선 사용.
"""

import logging
from typing import Callable, Iterable

import httpx

from src.config import Settings
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
    """배선(설정된 URL)에 따라 적절한 구현체를 생성한다."""

    def __init__(self, settings: Settings):
        self._settings = settings
        # DGX 서빙 모델 태그 집합. None = 모름(조회 실패/미주입) → 프로필 모델을 DGX 에
        # 태우지 않는다. set_dgx_catalog 로 부트스트랩에서 채운다.
        self._dgx_catalog: frozenset[str] | None = None

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

        from .embedding.sentence_transformers import SentenceTransformersProvider

        logger.info("Using local embedding model (CPU)")
        return SentenceTransformersProvider(
            model_name=self._settings.dev_embedding_model,
        )

    # === LLM 백엔드 선택 규칙 ===
    # ★DGX 위임(2026-07-16) 이후 이 메서드는 마스터 스위치가 아니다. dgx_llm_url이
    # 설정되면 primary는 DGX로 고정되고, 이 메서드는 **로컬 폴백 base를 만들 때만**
    # 호출된다(_wrap_with_dgx 참조).
    # 상용 퇴역(2026-07-16) 이후 고를 백엔드는 로컬 둘뿐이라 provider_mode 인자가 사라졌다 —
    # 백엔드는 이제 "MLX URL이 배선돼 있는가" 하나로 결정된다.
    def _llm_backend(self, server_url: str) -> str:
        """LLM 백엔드를 고른다 (DGX 미설정 시 primary, 설정 시 폴백).

        - MLX(server_url) 있으면 http, 없으면 ollama.
        """
        return "http" if server_url else "ollama"

    def get_router_llm(self) -> LLMProvider:
        return self._wrap_with_dgx(
            lambda: self._create_llm(
                server_url=self._settings.router_llm_server_url,
                local_model=self._settings.router_model,
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
                label="orchestration",
            ),
            "orchestration", self._dgx_model_for("orchestration"),
        )

    def set_dgx_catalog(self, names: Iterable[str]) -> None:
        """DGX 가 실제 서빙 중인 모델 태그 집합을 주입한다(부트스트랩에서 /api/tags 조회).

        None 으로 남으면 _split_model_request 는 프로필 모델을 DGX 에 태우지 않는다.
        """
        self._dgx_catalog = frozenset(n for n in names if n)

    def _split_model_request(self, requested: str, role: str) -> tuple[str, str]:
        """요청 모델을 (DGX 주 경로 모델, 폴백 모델)로 가른다.

        관리자 UI 의 main_model 드롭다운은 DGX /api/tags 가 준 실제 태그를 넣는다. 그런
        값만 DGX 주 경로에 태운다 — 예전엔 model_name 을 통째로 버려서 무엇을 고르든
        dgx_main_model 로만 돌았고 선택이 장식이었다.

        ★카탈로그에 있는 이름만 태우는 이유(중요):
        get_chat_model 에 오는 값은 DGX 태그라는 보장이 없다. 예전엔 alias "haiku" 가
        settings.router_model 인 "qwen3.5:9b" 로 풀려서 들어왔다 — 생김새가 DGX 태그와
        똑같지만 DGX 엔 없는 로컬 모델명이다. 접두사/슬래시 휴리스틱으로 가르면 이런 값이
        DGX 로 새어나가 매 요청 404("model not found") → 폴백이 되고, 폴백이 답을 주니
        겉보기엔 멀쩡한 채 DGX 가 통째로 우회된다(실측 확인).

        그 haiku 경로 자체는 상용 퇴역(2026-07-16)으로 사라졌지만, 같은 모양의 값은 계속
        들어온다: bootstrap 이 MLX /v1/models 에서 자동감지한 모델명("mlx-community/...",
        "qwen3.5:9b")이 폴백용으로 이 함수를 통과한다. 즉 원인은 alias 가 아니라 "DGX 태그와
        구분 안 되는 로컬 모델명"이고, 그건 그대로다. 그래서 "카탈로그에 있는가"만 믿는다.

        카탈로그를 모르면(조회 실패·미주입) 무조건 기본 DGX 모델 + 요청값은 폴백으로 —
        이는 이 함수가 생기기 전의 동작과 정확히 같다. 모르면 안전한 쪽으로 판단한다.
        """
        default = self._dgx_model_for(role)
        if not requested:
            return default, ""
        catalog = getattr(self, "_dgx_catalog", None)
        if catalog and requested in catalog:
            return requested, ""
        return default, requested

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
                label="main",
            ),
            "main", self._dgx_model_for("main"),
        )

    # === 하이브리드 라우팅용 명시 provider ===
    # 무료/패턴 콘텐츠($0)는 전용 로컬 MLX — 요청별 선택용.

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

    def _create_llm(self, server_url: str, local_model: str, label: str) -> LLMProvider:
        """결정론 경로용 LLMProvider 생성. 백엔드는 _llm_backend가 단일 결정."""
        system_prefix = get_locale().prompt("llm_system_prefix")
        backend = self._llm_backend(server_url)
        max_tokens = self._settings.llm_max_tokens

        if backend == "http":
            from .llm.http_llm import HttpLLMProvider

            logger.info("Using HTTP LLM server (%s): %s", label, server_url)
            return HttpLLMProvider(base_url=server_url, system_prefix=system_prefix, max_tokens=max_tokens)

        from .llm.ollama import OllamaProvider

        return OllamaProvider(
            base_url=self._settings.ollama_host, model=local_model,
            system_prefix=system_prefix,
        )

    def get_chat_model(self, model_name: str = ""):
        """에이전틱(LangGraph)용 langchain BaseChatModel. 백엔드는 _llm_backend가 단일 결정.

        model_name: MLX 자동감지 모델명 override (없으면 기본 모델).
        ImportError(langchain extra 미설치)는 호출부(bootstrap)에서 흡수 → agentic만 비활성.

        DGX 설정 시 ollama의 OpenAI 호환 엔드포인트(/v1)를 ChatOpenAI로 쓴다.
        langchain_ollama(ChatOllama)를 안 쓰는 이유: 이 이미지에 미설치이고, /v1로도
        tool calling이 정상 동작한다(qwen3.6:35b-a3b capabilities에 tools 포함).
        model_name 은 _split_model_request 가 가른다: DGX 태그면 주 경로에 태우고,
        MLX 로컬 모델명이면 DGX 에 없는 이름이라 폴백에만 넘긴다. 프로필의 main_model 을
        관리자 UI 가 DGX /api/tags 목록에서 고르게 되면서, 고른 값이 실제로 주 경로에
        반영되어야 하기 때문이다.

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

            dgx_model, fallback_model = self._split_model_request(model_name, "main")
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
            return primary.with_fallbacks([self._local_chat_model(fallback_model)])

        return self._local_chat_model(model_name)

    def _local_chat_model(self, model_name: str = ""):
        """DGX를 뺀 현행(로컬 MLX/ollama) langchain BaseChatModel.

        DGX 경로의 폴백으로도 쓰이므로 단독 호출 가능해야 한다.

        ChatOpenAI 를 쓰지만 OpenAI 벤더와 무관하다 — MLX 서버의 OpenAI 호환 /v1 shim 을
        치는 클라이언트일 뿐이다(api_key="not-needed"). 상용 퇴역과 함께 지우면 안 된다.
        """
        s = self._settings

        if self._llm_backend(s.main_llm_server_url) == "http":
            from langchain_openai import ChatOpenAI

            # streaming=True: agentic astream_events가 on_chat_model_stream을 토큰 단위로
            # 발생시키도록(MLX 서버는 stream=True를 지원). 미설정 시 최종 답변이 한 청크로 와
            # 프론트에서 한번에 렌더링됨.
            return ChatOpenAI(
                base_url=f"{s.main_llm_server_url.rstrip('/')}/v1",
                api_key="not-needed", model=model_name or s.main_model or "default",
                streaming=True,
            )

        from langchain_ollama import ChatOllama

        return ChatOllama(model=model_name or s.main_model, base_url=s.ollama_host, streaming=True)

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
            fallback_model = self._settings.reranker_model
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

        main_llm 하나만 등록한다. 상용 퇴역(2026-07-16) 전에는 AIP_PROVIDER_ENABLE_ANTHROPIC/
        _OPENAI 로 벤더 provider 를 추가 등록할 수 있었지만, 등록할 벤더가 없어졌다.
        """
        registry = ProviderRegistry()
        registry.register_inplace(self.get_main_llm())
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
