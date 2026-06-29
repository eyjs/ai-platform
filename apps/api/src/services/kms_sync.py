"""KMS 문서 동기화 서비스.

Webhook으로 수신된 이벤트를 처리하여 ai-platform 벡터 DB와 동기화한다.
KMS API에서 문서 메타데이터와 파일을 조회하고 IngestPipeline으로 처리한다.
"""

import uuid
from datetime import datetime, timezone

import httpx

from src.config import Settings
from src.infrastructure.vector_store import VectorStore
from src.observability.logging import get_logger
from src.pipeline.ingest import IngestPipeline
from src.services.domain_mapping import resolve_product_domain

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
        # VLM 보강 큐 전용: DocForge /v1/parse/sync 를 vlm_mode=full 로 직접 호출(긴 대기).
        self._docforge_url = settings.docforge_url
        self._docforge_internal_key = settings.docforge_internal_key
        self._store = vector_store
        self._pipeline = ingest_pipeline
        # 테넌트 격리(A2): tenant_id 는 NOT NULL. webhook 페이로드엔 테넌트가 없으므로
        # 기본 테넌트로 스탬핑한다 (KMS 가 단일 테넌트 운영). KMS 가 향후 테넌트를
        # webhook data 로 전달하면 data 우선으로 확장.
        self._default_tenant_id = settings.default_tenant_id or "default"

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

        # 배치(placement) 전이면 도메인이 없다 — 검색 스코프가 정해지지 않았으므로
        # 적재를 보류한다. PlacementsService 가 배치 후 dispatch 하는
        # document.updated(domainCodes 포함)가 실제 적재를 트리거한다.
        # 이로써 생성 시점의 빈-도메인 document.created 가 스푸리어스 문서를 만드는
        # 이중 적재를 방지한다 (create+placement 경쟁 제거).
        if not domain_code:
            logger.info("kms_sync_awaiting_placement", document_id=document_id)
            return {"status": "skipped", "reason": "awaiting placement (no domain)"}

        # ── 도메인 매핑 (근본해결) ─────────────────────────────────────────
        # KMS 는 회사중심 도메인(DB-DAMAGE)으로 배치하지만, 챗봇 프로필은 상품중심
        # 도메인(자동차보험/건강보험/…)으로 검색 스코프를 건다. KMS 가 webhook 으로
        # 전달한 categoryPath(["DB-DAMAGE","자동차보험","개인용"])로 상품도메인을 해석한다.
        # KMS=분류 SoT(ADR-009), ai-platform 은 해석만 — 매핑은 seeds/domain_mapping.yaml.
        company_domain = domain_code
        category_path = data.get("categoryPath", []) or []
        product_domain = resolve_product_domain(company_domain, category_path)
        if product_domain:
            domain_code = product_domain
        else:
            # 미매핑/부재(구 KMS·타 consumer·매핑 미정의 카테고리) → 회사도메인 fallback.
            # 조용한 누락 0: 반드시 WARN 으로 가시화한다(메트릭/로그 신호).
            logger.warning(
                "kms_sync_domain_unmapped",
                company_domain=company_domain,
                category_path=category_path,
                document_id=document_id,
            )

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
            external_id=document_id,
            tenant_id=data.get("tenantId") or self._default_tenant_id,
        )

        # external_id 설정 (insert_document에서 이미 처리되지만 명시적 업데이트)
        if result.get("document_id"):
            await self._set_external_id(result["document_id"], document_id)

        # 파싱결과(조립 MD)를 KMS 로 콜백 — 원본↔MD 비교 표시용(Path B).
        # markdown 은 job 큐 로그에 싣지 않도록 pop 후 별도 전송한다.
        markdown = result.pop("markdown", None)
        # PDF 는 비동기 VLM 보강 큐 대상 → fast 결과와 함께 vlm_status=PENDING 으로 표시.
        # (워커가 enhance job 적재, enhance_document 가 PROCESSING→DONE 으로 전이)
        is_pdf = mime_type == "application/pdf"
        await self._post_parse_result(
            document_id, markdown, parse_status="PARSED",
            vlm_status="PENDING" if is_pdf else None,
        )

        logger.info(
            "kms_sync_complete",
            document_id=document_id,
            aip_doc_id=result.get("document_id"),
            chunks=result.get("chunks", 0),
        )
        # 워커가 PDF 일 때만 vlm_enhance 큐에 적재하도록 mime 을 노출한다.
        result["mime_type"] = mime_type
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

    async def _post_parse_result(
        self,
        document_id: str,
        markdown: str | None,
        parse_status: str = "PARSED",
        error: str | None = None,
        vlm_status: str | None = None,
    ) -> None:
        """파싱된 마크다운을 KMS 로 콜백 전송한다 (POST /processing/:id/content).

        graceful: 콜백 실패가 ingest 를 실패시키지 않는다. KMS 가 비교 표시용으로만 쓰므로
        전송 실패 시 WARN 후 통과한다(다음 재처리 때 갱신).
        """
        if not self._kms_url or not self._internal_key:
            logger.warning("kms_parse_callback_skipped", document_id=document_id, reason="no kms url/key")
            return

        url = f"{self._kms_url}/processing/{document_id}/content"
        payload = {
            "rawText": markdown,
            "parseStatus": parse_status,
            "parsedAt": datetime.now(timezone.utc).isoformat(),
        }
        if vlm_status is not None:
            payload["vlmStatus"] = vlm_status
        if error:
            payload["error"] = error
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url, json=payload, headers={"X-Internal-Key": self._internal_key},
                )
                resp.raise_for_status()
            logger.info(
                "kms_parse_callback_ok",
                document_id=document_id,
                parse_status=parse_status,
                vlm_status=vlm_status,
                chars=len(markdown or ""),
            )
        except httpx.HTTPError as e:
            logger.warning("kms_parse_callback_failed", document_id=document_id, error=str(e))

    async def _post_vlm_status(
        self, document_id: str, vlm_status: str, error: str | None = None,
    ) -> None:
        """VLM 보강 상태만 KMS 에 부분 업데이트한다 (markdown 미포함 → 기존 본문 보존).

        rawText 키를 보내지 않으므로 KMS 부분 업데이트가 parsed_markdown 을 건드리지 않는다.
        """
        if not self._kms_url or not self._internal_key:
            return
        url = f"{self._kms_url}/processing/{document_id}/content"
        payload = {"vlmStatus": vlm_status}
        if error:
            payload["error"] = error
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url, json=payload, headers={"X-Internal-Key": self._internal_key},
                )
                resp.raise_for_status()
            logger.info("kms_vlm_status_ok", document_id=document_id, vlm_status=vlm_status)
        except httpx.HTTPError as e:
            logger.warning("kms_vlm_status_failed", document_id=document_id, error=str(e))

    async def enhance_document(self, document_id: str, data: dict) -> dict:
        """비동기 VLM 보강: PDF 를 DocForge full-page VLM 으로 재파싱 → KMS 본문 교체.

        fast 파싱과 분리된 백그라운드 큐('vlm_enhance')에서 실행되므로 동기 타임아웃이 없다.
        상태 전이: PENDING(적재) → PROCESSING(시작) → DONE(완료) / FAILED(실패).
        """
        doc_meta = await self._fetch_document_meta(document_id)
        if not doc_meta:
            return {"status": "skipped", "reason": "document not found"}
        file_type = doc_meta.get("fileType", "")
        mime_type = doc_meta.get("mimeType") or _FILETYPE_TO_MIME.get(file_type, "")
        if mime_type != "application/pdf":
            # PDF 만 VLM 보강 대상 (CSV/MD 는 테이블 구조 복원 불필요)
            return {"status": "skipped", "reason": f"not a pdf ({mime_type})"}

        file_name = doc_meta.get("fileName") or f"{document_id}.pdf"
        await self._post_vlm_status(document_id, "PROCESSING")

        file_bytes = await self._download_file(document_id)
        if not file_bytes:
            await self._post_vlm_status(document_id, "FAILED", error="file download failed")
            return {"status": "failed", "reason": "file download failed"}

        markdown = await self._docforge_parse_vlm(file_bytes, file_name, mime_type)
        if not markdown:
            await self._post_vlm_status(document_id, "FAILED", error="docforge vlm parse failed")
            return {"status": "failed", "reason": "docforge vlm parse failed"}

        # 본문 교체 + DONE
        await self._post_parse_result(
            document_id, markdown, parse_status="PARSED", vlm_status="DONE",
        )
        logger.info("kms_vlm_enhance_complete", document_id=document_id, chars=len(markdown))
        return {"status": "enhanced", "document_id": document_id, "chars": len(markdown)}

    async def _docforge_parse_vlm(
        self, file_bytes: bytes, file_name: str, mime_type: str,
    ) -> str | None:
        """DocForge /v1/parse/sync 를 vlm_mode=full 로 호출(긴 대기). 마크다운 반환."""
        if not self._docforge_url:
            logger.warning("docforge_url_missing")
            return None
        url = f"{self._docforge_url}/v1/parse/sync"
        headers = {}
        if self._docforge_internal_key:
            headers["X-Internal-Key"] = self._docforge_internal_key
        try:
            # 백그라운드 잡이라 긴 타임아웃 허용(full-page VLM 은 문서당 수십 분 가능).
            async with httpx.AsyncClient(timeout=httpx.Timeout(2400.0)) as client:
                resp = await client.post(
                    url,
                    files={"file": (file_name, file_bytes, mime_type)},
                    data={"vlm_mode": "full"},
                    headers=headers,
                )
                resp.raise_for_status()
                body = resp.json()
            if not body.get("success"):
                logger.warning("docforge_vlm_parse_error", body=str(body)[:200])
                return None
            return (body.get("data") or {}).get("markdown")
        except httpx.HTTPError as e:
            logger.warning("docforge_vlm_http_error", error=str(e))
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
