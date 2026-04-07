"""벡터 저장소 추상 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional


class AbstractVectorStore(ABC):
    """벡터 저장소 ABC. 모든 consumer는 이 타입에 의존한다."""

    @abstractmethod
    async def connect(self, min_size: int = 5, max_size: int = 50) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # -- 문서 관리 --

    @abstractmethod
    async def insert_document(
        self,
        title: str,
        domain_code: str,
        file_name: str | None = None,
        file_hash: str | None = None,
        security_level: str = "PUBLIC",
        source_url: str | None = None,
        external_id: str | None = None,
        metadata: dict | None = None,
    ) -> str: ...

    @abstractmethod
    async def insert_chunks(
        self,
        document_id: str,
        chunks: List[dict],
        embeddings: List[List[float]],
        domain_code: str = "",
        security_level: str = "PUBLIC",
    ) -> List[str]: ...

    @abstractmethod
    async def delete_document_chunks(self, document_id: str) -> int: ...

    @abstractmethod
    async def get_chunk_count(self, document_id: str) -> int: ...

    # -- 검색 --

    @abstractmethod
    async def search(
        self,
        embedding: List[float],
        limit: int = 10,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        max_security_level: Optional[str] = None,
    ) -> List[dict]: ...

    @abstractmethod
    async def hybrid_search(
        self,
        embedding: List[float],
        text_query: str,
        limit: int = 10,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        vector_weight: float = 0.5,
        max_security_level: Optional[str] = None,
    ) -> List[dict]: ...

    @abstractmethod
    async def get_neighbor_chunks(
        self, document_id: str, chunk_indices: list[int],
    ) -> list[dict]: ...

    # -- ID 매핑 --

    @abstractmethod
    async def get_external_ids(self, aip_doc_ids: list[str]) -> dict[str, str]: ...

    @abstractmethod
    async def get_aip_id_by_external(self, external_id: str) -> str | None: ...

    @abstractmethod
    async def get_top_chunks_by_doc(
        self,
        document_id: str,
        limit: int = 2,
        max_security_level: str | None = None,
    ) -> list[dict]: ...

    @abstractmethod
    async def get_aip_ids_by_externals(
        self, external_ids: list[str],
    ) -> dict[str, dict]: ...

    # -- Progressive Disclosure 검색 --

    @abstractmethod
    async def metadata_search(
        self,
        embedding: List[float],
        text_query: str,
        limit: int = 10,
        domain_codes: Optional[List[str]] = None,
        allowed_doc_ids: Optional[List[str]] = None,
        max_security_level: Optional[str] = None,
    ) -> List[dict]:
        """메타데이터 전용 검색 (content 제외). Progressive Disclosure Level 1."""
        ...

    @abstractmethod
    async def fetch_chunks_by_doc_ids(
        self,
        doc_ids: list[str],
        limit_per_doc: int = 5,
        max_security_level: Optional[str] = None,
    ) -> List[dict]:
        """doc_ids 기반 청크 본문 로드. Progressive Disclosure Level 2+."""
        ...
