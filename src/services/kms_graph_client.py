"""KMS 지식그래프 API 클라이언트.

KMS의 /knowledge-graph/rag-context API를 호출하여
문서 간 관계(온톨로지) 정보를 조회한다.
"""

import httpx

from src.observability.logging import get_logger

logger = get_logger(__name__)


class KmsGraphClient:
    """KMS 지식그래프 API HTTP 클라이언트."""

    def __init__(self, kms_api_url: str, kms_internal_key: str):
        self._url = kms_api_url.rstrip("/")
        self._key = kms_internal_key

    @property
    def is_configured(self) -> bool:
        return bool(self._url and self._key)

    async def get_rag_context(
        self,
        document_id: str,
        depth: int = 1,
        max_documents: int = 999,
    ) -> dict | None:
        """GET /knowledge-graph/rag-context/{documentId}

        Returns:
            관련 문서 목록 dict 또는 실패 시 None.
        """
        url = f"{self._url}/knowledge-graph/rag-context/{document_id}"
        params = {"depth": depth, "maxDocuments": max_documents}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url,
                    params=params,
                    headers={"X-Internal-Key": self._key},
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning("kms_graph_api_error", url=url, error=str(e))
            return None

    async def close(self) -> None:
        """리소스 정리."""
        pass
