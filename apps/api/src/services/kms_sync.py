"""KMS 문서 동기화 서비스.

Webhook으로 수신된 이벤트를 처리하여 ai-platform 벡터 DB와 동기화한다.
KMS API에서 문서 메타데이터와 파일을 조회하고 IngestPipeline으로 처리한다.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx

from src.config import Settings
from src.domain.models import UNPLACED_DOMAIN
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

# holding 도메인(UNPLACED_DOMAIN)은 domain/models 에 정의 — 검색 계층(vector_search)이
# 동일 상수로 항상 제외하여 "임베딩됨·비노출" 불변식을 보장한다. 여기선 재노출(import)만.


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

    async def sync_document(self, document_id: str, data: dict, event: str = "") -> dict:
        """KMS 문서를 벡터 DB에 동기화한다 (임베딩·배치 분리 설계).

        업로드(document.created)는 도메인 없이 오지만 **즉시** 파싱·청킹·임베딩하여
        holding 도메인(__unplaced__)으로 적재한다("업로드=청크 생성" 보장). 배치
        (document.updated + domainCodes)가 오면 **재임베딩 없이** doc/chunks 의
        domain_code 만 in-place 재태깅한다(임베딩은 콘텐츠당 1회). 파일 재업로드
        (document.file_uploaded)만 재적재하고, 순수 메타 갱신은 스킵한다.
        """
        if not self._kms_url or not self._internal_key:
            raise RuntimeError("KMS API URL or Internal Key not configured")

        doc_meta = await self._fetch_document_meta(document_id)
        if not doc_meta:
            logger.warning("kms_document_not_found", document_id=document_id)
            return {"status": "skipped", "reason": "document not found in KMS"}

        # 배치 이벤트가 실은 회사도메인 → 상품도메인 해석 (미배치=None)
        resolved_domain = self._resolve_domain(document_id, data)

        # 같은 문서의 create/placement 동시 처리를 직렬화한다(이중 행 방지) — PG advisory lock.
        # max_concurrent 워커에서 create(→__unplaced__)와 placement(→상품도메인)가 동시에
        # 존재확인을 통과하면 external_id 가 같아도 domain_code 가 달라 2행이 생길 수 있다.
        async with self._doc_lock(document_id):
            return await self._sync_locked(
                document_id, data, event, doc_meta, resolved_domain,
            )

    async def _sync_locked(
        self,
        document_id: str,
        data: dict,
        event: str,
        doc_meta: dict,
        resolved_domain: str | None,
    ) -> dict:
        """advisory lock 하에서 실행되는 존재확인 + (재)적재/재태깅 본체."""
        existing = await self._get_existing(document_id)

        file_name = doc_meta.get("fileName") or doc_meta.get("originalName", "")
        title = doc_meta.get("title") or file_name or "Untitled"
        file_type = doc_meta.get("fileType", "")
        mime_type = doc_meta.get("mimeType") or _FILETYPE_TO_MIME.get(file_type, "")
        raw_security = doc_meta.get("securityLevel", "PUBLIC")
        security_level = raw_security if raw_security in _SECURITY_MAP else "PUBLIC"
        status = doc_meta.get("lifecycle") or doc_meta.get("status", "DRAFT")

        # ── 이미 적재됨 & 콘텐츠 불변(파일 재업로드 아님) → 재임베딩 없이 메타만 갱신 ──
        # 배치(placement)=도메인 재태깅, 보안등급 변경=청크까지 전파(다운그레이드 누출 방지).
        # domain·security 둘 다 그대로면 스킵(멱등). 파일 재업로드는 아래 (재)적재로 간다.
        if existing and event != "document.file_uploaded":
            target_domain = resolved_domain or existing["domain_code"]
            unchanged = (
                target_domain == existing["domain_code"]
                and security_level == existing["security_level"]
            )
            if unchanged:
                return {
                    "status": "skipped",
                    "reason": "already synced (no change)",
                    "document_id": document_id,
                    "domain_code": existing["domain_code"],
                }
            updated_rows = await self._retag_and_refresh(
                existing["id"], target_domain, security_level,
            )
            logger.info(
                "kms_sync_retagged",
                document_id=document_id,
                domain_code=target_domain,
                prev_domain=existing["domain_code"],
                security_level=security_level,
                updated_rows=updated_rows,
            )
            return {
                "status": "retagged",
                "document_id": document_id,
                "domain_code": target_domain,
                # placed PDF 는 배치 시점에 워커가 VLM 보강 큐잉을 판단한다.
                "mime_type": mime_type,
            }

        # ── (재)적재: 신규 업로드 또는 파일 재업로드 ──
        # 대상 도메인: 해석된 상품도메인 > 기존 도메인 > holding(미배치)
        domain_code = (
            resolved_domain
            or (existing["domain_code"] if existing else None)
            or UNPLACED_DOMAIN
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
            security_level=security_level,
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
        # PDF 는 비동기 VLM 보강 큐 대상. 단 **배치된(placed) 문서만** — 미배치(__unplaced__)
        # 는 승인 전이므로 KMS 본문 변조·수분짜리 VLM 낭비를 막고 vlm_status 도 PENDING 으로
        # 표시하지 않는다. 배치 시점(document.updated)에 워커가 다시 판단한다.
        is_pdf = mime_type == "application/pdf"
        placed = domain_code != UNPLACED_DOMAIN
        vlm_pending = is_pdf and placed
        await self._post_parse_result(
            document_id, markdown, parse_status="PARSED",
            vlm_status="PENDING" if vlm_pending else None,
        )

        logger.info(
            "kms_sync_complete",
            document_id=document_id,
            aip_doc_id=result.get("document_id"),
            domain_code=domain_code,
            unplaced=(domain_code == UNPLACED_DOMAIN),
            chunks=result.get("chunks", 0),
        )
        # 워커가 배치된 PDF 일 때만 vlm_enhance 큐잉하도록 mime·domain 을 노출한다.
        result["mime_type"] = mime_type
        result["domain_code"] = domain_code
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

    @asynccontextmanager
    async def _doc_lock(self, document_id: str):
        """문서 단위 PG 세션 advisory lock — create/placement 동시 처리 직렬화.

        PostgreSQL 단일 스택 원칙 준수(별도 락 인프라 없음). hashtextextended 로
        document_id(text) → bigint 락 키. pool 미연결이면 no-op(테스트/부트스트랩).
        """
        if not self._store.pool:
            yield
            return
        async with self._store.pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", document_id)
            try:
                yield
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", document_id)

    def _resolve_domain(self, document_id: str, data: dict) -> str | None:
        """webhook data 의 domainCodes+categoryPath 를 상품도메인으로 해석한다.

        배치 전(domainCodes 부재)이면 None 을 반환한다(→ holding 도메인 적재).
        domainCodes 는 있으나 매핑 미정의면 회사도메인으로 fallback + WARN(조용한 누락 0).

        KMS 는 회사중심 도메인(DB-DAMAGE)으로 배치하지만 챗봇 프로필은 상품중심
        도메인(자동차보험/건강보험/…)으로 검색 스코프를 건다. categoryPath
        (["DB-DAMAGE","자동차보험","개인용"])로 상품도메인을 해석한다.
        KMS=분류 SoT(ADR-009), ai-platform 은 해석만 — 매핑은 seeds/domain_mapping.yaml.
        """
        domain_codes = data.get("domainCodes", [])
        if not domain_codes:
            return None
        company_domain = domain_codes[0]
        category_path = data.get("categoryPath", []) or []
        product_domain = resolve_product_domain(company_domain, category_path)
        if product_domain:
            return product_domain
        logger.warning(
            "kms_sync_domain_unmapped",
            company_domain=company_domain,
            category_path=category_path,
            document_id=document_id,
        )
        return company_domain

    async def _get_existing(self, external_id: str) -> dict | None:
        """external_id 로 이미 적재된 aip 문서(id, domain_code, security_level)를 조회한다."""
        if not self._store.pool:
            return None
        async with self._store.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, domain_code, security_level FROM documents WHERE external_id = $1",
                external_id,
            )
        return (
            {
                "id": row["id"],
                "domain_code": row["domain_code"],
                "security_level": row["security_level"],
            }
            if row
            else None
        )

    async def _retag_and_refresh(
        self, doc_id, domain_code: str, security_level: str,
    ) -> int:
        """재임베딩 없이 doc+chunks 의 domain_code·security_level 만 in-place 갱신한다.

        임베딩(비싼 연산)은 콘텐츠당 1회만. 배치(도메인 재태깅)와 보안등급 변경 전파를
        메타 갱신으로 처리한다 — 특히 security 변경을 청크까지 반영해야 다운그레이드
        누출을 막는다. doc_id 는 호출측 _get_existing 결과를 재사용(중복 SELECT 제거).
        반환값은 갱신된 chunk 행 수.
        """
        if not self._store.pool:
            return 0
        async with self._store.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE documents SET domain_code = $2, security_level = $3 WHERE id = $1",
                    doc_id, domain_code, security_level,
                )
                result = await conn.execute(
                    "UPDATE document_chunks SET domain_code = $2, security_level = $3 "
                    "WHERE document_id = $1",
                    doc_id, domain_code, security_level,
                )
                return int(result.split()[-1])

    async def _set_external_id(self, aip_doc_id: str, kms_doc_id: str) -> None:
        """ai-platform 문서에 KMS external_id를 설정한다."""
        if not self._store.pool:
            return
        async with self._store.pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET external_id = $1 WHERE id = $2",
                kms_doc_id, uuid.UUID(aip_doc_id),
            )
