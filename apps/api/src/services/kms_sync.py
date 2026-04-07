"""KMS 문서 동기화 서비스.

Webhook으로 수신된 이벤트를 처리하여 ai-platform 벡터 DB와 동기화한다.
KMS API에서 문서 메타데이터와 파일을 조회하고 IngestPipeline으로 처리한다.
"""

import uuid

import httpx

from src.config import Settings
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.pipeline.ingest import IngestPipeline

logger = get_logger(__name__)

# KMS 보안등급 -> ai-platform 보안등급 매핑 (동일)
_SECURITY_MAP = {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"}

# KMS fileType -> MIME type 매핑
_FILETYPE_TO_MIME = {
    "pdf": "application/pdf",
    "md": "text/markdown",
    "csv": "text/csv",
}


class KmsSyncService:
    """KMS 문서를 ai-platform 벡터 DB에 동기화한다."""

    def __init__(
        self,
        settings: Settings,
        vector_store: VectorStore,
        ingest_pipeline: IngestPipeline,
    ):
        self._kms_url = settings.kms_api_url
        self._internal_key = settings.kms_internal_key
        self._store = vector_store
        self._pipeline = ingest_pipeline

    async def sync_document(self, document_id: str, data: dict) -> dict:
        """KMS에서 문서를 가져와 벡터 DB에 동기화한다."""
        if not self._kms_url or not self._internal_key:
            raise RuntimeError("KMS API URL or Internal Key not configured")

        doc_meta = await self._fetch_document_meta(document_id)
        if not doc_meta:
            logger.warning("kms_document_not_found", document_id=document_id)
            return {"status": "skipped", "reason": "document not found in KMS"}

        file_name = doc_meta.get("fileName") or doc_meta.get("originalName", "")
        title = doc_meta.get("title") or file_name or "Untitled"
        file_type = doc_meta.get("fileType", "")
        mime_type = doc_meta.get("mimeType") or _FILETYPE_TO_MIME.get(file_type, "")
        security_level = doc_meta.get("securityLevel", "PUBLIC")
        status = doc_meta.get("lifecycle") or doc_meta.get("status", "DRAFT")

        # 도메인 코드: webhook data에서 가져오거나 빈 문자열
        domain_codes = data.get("domainCodes", [])
        domain_code = domain_codes[0] if domain_codes else ""

        # 파일 다운로드
        file_bytes = await self._download_file(document_id)
        if not file_bytes:
            logger.warning("kms_file_download_failed", document_id=document_id)
            return {"status": "skipped", "reason": "file download failed"}

        # 기존 동기화 데이터 삭제 (external_id 기준)
        await self._delete_by_external_id(document_id)

        # IngestPipeline으로 처리
        metadata = {
            "kms_document_id": document_id,
            "lifecycle_status": status,
            "category_names": data.get("categoryNames", []),
        }

        result = await self._pipeline.ingest_text(
            title=title,
            domain_code=domain_code,
            file_name=file_name,
            security_level=security_level if security_level in _SECURITY_MAP else "PUBLIC",
            metadata=metadata,
            file_bytes=file_bytes,
            mime_type=mime_type,
        )

        # external_id 설정 (insert_document에서 이미 처리되지만 명시적 업데이트)
        if result.get("document_id"):
            await self._set_external_id(result["document_id"], document_id)

        logger.info(
            "kms_sync_complete",
            document_id=document_id,
            aip_doc_id=result.get("document_id"),
            chunks=result.get("chunks", 0),
        )
        return result

    async def delete_document(self, document_id: str) -> dict:
        """KMS 문서 삭제 시 벡터 DB에서도 삭제한다."""
        deleted = await self._delete_by_external_id(document_id)
        logger.info("kms_delete_complete", document_id=document_id, deleted_chunks=deleted)
        return {"status": "deleted", "document_id": document_id, "deleted_chunks": deleted}

    async def update_lifecycle(self, document_id: str, status: str) -> dict:
        """라이프사이클 상태만 메타데이터에 업데이트한다."""
        if not self._store.pool:
            raise RuntimeError("VectorStore not connected")

        async with self._store.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE documents
                SET metadata = jsonb_set(
                    COALESCE(metadata::jsonb, '{}'::jsonb),
                    '{lifecycle_status}',
                    to_jsonb($2::text)
                )
                WHERE external_id = $1
                """,
                document_id, status,
            )

        count = int(result.split()[-1])
        logger.info("kms_lifecycle_updated", document_id=document_id, status=status, updated=count)
        return {"status": "updated", "document_id": document_id, "lifecycle_status": status}

    async def _fetch_document_meta(self, document_id: str) -> dict | None:
        """KMS API에서 문서 메타데이터를 조회한다."""
        url = f"{self._kms_url}/documents/{document_id}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url, headers={"X-Internal-Key": self._internal_key},
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as e:
            logger.error("kms_api_error", url=url, error=str(e))
            raise

    async def _download_file(self, document_id: str) -> bytes | None:
        """KMS API에서 문서 파일을 다운로드한다."""
        url = f"{self._kms_url}/documents/{document_id}/file"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(
                    url, headers={"X-Internal-Key": self._internal_key},
                )
                if resp.status_code != 200:
                    return None
                return resp.content
        except httpx.HTTPError as e:
            logger.error("kms_download_error", url=url, error=str(e))
            return None

    async def _delete_by_external_id(self, external_id: str) -> int:
        """external_id로 문서와 청크를 삭제한다."""
        if not self._store.pool:
            return 0
        async with self._store.pool.acquire() as conn:
            async with conn.transaction():
                # 문서 ID 조회
                row = await conn.fetchrow(
                    "SELECT id FROM documents WHERE external_id = $1", external_id,
                )
                if not row:
                    return 0
                doc_id = row["id"]
                # 청크 삭제
                result = await conn.execute(
                    "DELETE FROM document_chunks WHERE document_id = $1", doc_id,
                )
                chunk_count = int(result.split()[-1])
                # 문서 삭제
                await conn.execute("DELETE FROM documents WHERE id = $1", doc_id)
                return chunk_count

    async def _set_external_id(self, aip_doc_id: str, kms_doc_id: str) -> None:
        """ai-platform 문서에 KMS external_id를 설정한다."""
        if not self._store.pool:
            return
        async with self._store.pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET external_id = $1 WHERE id = $2",
                kms_doc_id, uuid.UUID(aip_doc_id),
            )
