"""KMS 미설정 시 사용하는 Null Object."""

from __future__ import annotations


class NullKmsClient:
    """KMS API가 설정되지 않았을 때 사용. 모든 호출에 빈 결과를 반환한다."""

    @property
    def is_configured(self) -> bool:
        return False

    async def get_rag_context(
        self,
        document_id: str,
        depth: int = 1,
        max_documents: int = 999,
    ) -> dict | None:
        return None

    async def close(self) -> None:
        pass
