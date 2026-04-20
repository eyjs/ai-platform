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
    async def generate(self, prompt: str, system: str = "") -> str: ...

    @abstractmethod
    async def generate_json(self, prompt: str, system: str = "") -> dict: ...

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        """토큰 단위 스트리밍. 기본 구현은 generate() 결과를 한 번에 yield."""
        yield await self.generate(prompt, system=system)

    async def generate_stream_typed(self, prompt: str, system: str = "") -> AsyncIterator[StreamChunk]:
        """thinking/answer 구분 스트리밍. 기본 구현은 전부 answer로 전달."""
        async for token in self.generate_stream(prompt, system):
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
