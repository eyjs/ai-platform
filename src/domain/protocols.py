"""레이어 간 공유 프로토콜."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class KmsGraphProtocol(Protocol):
    """KMS 지식그래프 클라이언트 프로토콜."""

    @property
    def is_configured(self) -> bool: ...

    async def get_rag_context(
        self,
        document_id: str,
        depth: int = 1,
        max_documents: int = 999,
    ) -> dict | None: ...

    async def close(self) -> None: ...
