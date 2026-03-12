"""프로바이더 추상 인터페이스.

LLM, Embedding, Reranker, Parsing 4종.
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, List


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

    @abstractmethod
    async def generate(self, prompt: str, system: str = "") -> str: ...

    @abstractmethod
    async def generate_json(self, prompt: str, system: str = "") -> dict: ...

    async def generate_stream(self, prompt: str, system: str = "") -> AsyncIterator[str]:
        """토큰 단위 스트리밍. 기본 구현은 generate() 결과를 한 번에 yield."""
        yield await self.generate(prompt, system=system)


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
