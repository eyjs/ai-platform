"""프로바이더 추상 인터페이스.

LLM, Embedding, Reranker, Parsing 4종.

Task 002: Provider Capability Metadata 추가.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, List


@dataclass(frozen=True)
class StreamChunk:
    """스트리밍 청크. kind로 thinking/answer 구분."""
    kind: str   # "thinking" | "answer"
    content: str


@dataclass(frozen=True)
class ProviderCapability:
    """Provider 메타데이터. Router Policy 가 후보 선택에 사용.

    provider_id 는 전역 유일. YAML `providers:` 블록과 registry 키가 동일해야 한다.
    stub=True 인 경우 인터페이스만 구현. generate*() 호출 시 NotImplementedError.
    """
    provider_id: str
    supports_tool_use: bool
    supports_streaming: bool
    max_context: int
    cost_per_1k_tokens: float
    stub: bool = False
    supports_prompt_caching: bool = False


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, text: str) -> List[float]: ...

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...


class LLMProvider(ABC):
    _system_prefix: str = ""

    def _build_system(self, system: str) -> str:
        """system_prefix를 자동 주입한다."""
        if not self._system_prefix:
            return system
        if not system:
            return self._system_prefix
        return f"{self._system_prefix}\n\n{system}"

    @staticmethod
    def _combine_system(
        system: str = "", cacheable_system: str = "", volatile_system: str = "",
    ) -> str:
        """캐싱 미지원 백엔드용 — cacheable/volatile system을 단일 system으로 결합한다.

        AnthropicLLMProvider는 프롬프트 캐싱을 위해 generate(cacheable_system, volatile_system)
        시그니처를 쓴다. MLX/Ollama/OpenAI 등 캐싱 없는 백엔드는 이 둘을 이어붙여 하나의
        system으로 처리한다(의미 동일, 캐시 경계만 없음). 하위호환: 신규 인자가 모두 비면
        기존 system 사용.
        """
        if cacheable_system or volatile_system:
            return "\n\n".join(p for p in (cacheable_system, volatile_system) if p)
        return system

    @property
    def capability(self) -> ProviderCapability:
        """기본 capability. 각 구현체는 이 property 를 오버라이드해야 한다.

        기본값은 안전한 local-provider 기준. 서브클래스가 반드시 재정의.
        """
        return ProviderCapability(
            provider_id=self.__class__.__name__.lower(),
            supports_tool_use=False,
            supports_streaming=True,
            max_context=8192,
            cost_per_1k_tokens=0.0,
            stub=False,
        )

    async def is_available(self) -> bool:
        """네트워크/헬스 체크. 기본 True. stub 도 True 반환."""
        return True

    @abstractmethod
    async def generate(self, prompt: str, system: str = "") -> str:
        """최종 답변 생성.

        구현체는 `max_tokens: int | None = None` 키워드 인자를 추가로 받아
        per-call 출력 토큰 캡을 지원한다(None = 인스턴스 기본값).
        """
        ...

    @abstractmethod
    async def generate_json(self, prompt: str, system: str = "") -> dict: ...

    async def generate_stream(
        self, prompt: str, system: str = "", max_tokens: "int | None" = None,
    ) -> AsyncIterator[str]:
        """토큰 단위 스트리밍. 기본 구현은 generate() 결과를 한 번에 yield."""
        if max_tokens is None:
            # 캡 미지정이면 구버전 generate 시그니처(테스트 더블 포함)와 호환 유지
            yield await self.generate(prompt, system=system)
        else:
            yield await self.generate(prompt, system=system, max_tokens=max_tokens)

    async def generate_stream_typed(
        self, prompt: str, system: str = "",
        cacheable_system: str = "", volatile_system: str = "",
        max_tokens: "int | None" = None,
    ) -> AsyncIterator[StreamChunk]:
        """thinking/answer 구분 스트리밍. 기본 구현은 전부 answer로 전달.

        cacheable/volatile은 캐싱 미지원 백엔드용으로 단일 system으로 결합한다
        (캐싱 지원 구현체는 이 메서드를 오버라이드해 경계를 유지).
        """
        combined = self._combine_system(system, cacheable_system, volatile_system)
        async for token in self.generate_stream(prompt, combined, max_tokens=max_tokens):
            yield StreamChunk(kind="answer", content=token)


class RerankerProvider(ABC):
    """검색 결과를 질문-문서 관련성 기준으로 재정렬하는 리랭커."""

    @abstractmethod
    async def rerank(
        self, query: str, documents: List[str], top_k: int = 10
    ) -> List[dict]:
        """문서를 관련성 기준으로 재정렬한다.

        Returns:
            [{"index": 원래_인덱스, "score": 관련성_점수}] (score 내림차순)
        """
        ...


class ParsingProvider(ABC):
    @abstractmethod
    async def parse(self, file_bytes: bytes, mime_type: str) -> str: ...

    @abstractmethod
    def supported_types(self) -> List[str]: ...


class ProviderUnavailableError(RuntimeError):
    """Provider 가 호출 불가 상태 (stub, 네트워크 실패, rate limit 등).

    Fallback chain 종료 시 Gateway 로 전파되어 502 응답의 근거가 된다.
    """

    def __init__(self, provider_id: str, reason: str):
        self.provider_id = provider_id
        self.reason = reason
        super().__init__(f"Provider '{provider_id}' unavailable: {reason}")
