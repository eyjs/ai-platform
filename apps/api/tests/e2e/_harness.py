"""E2E 오케스트레이션 헬퍼 (P3 Step 17a).

KMS 업로드/배치 → ai-platform 적재 단언을 재사용 가능한 함수로 제공한다.
모든 cross-service 호출은 HTTP / DB 경유 (프로덕션 코드 import 금지, 앱간 경계 준수).
17b(실패주입) / 17c(게이팅) 가 이 헬퍼를 공유한다.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import httpx

#: 골든패스 도메인 (핸드오프 §6 시나리오 D).
DEFAULT_DOMAIN_CODE = "DB-DAMAGE"


def _auth_headers(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


# ---------------------------------------------------------------------------
# KMS 측 (HTTP)
# ---------------------------------------------------------------------------

async def kms_upload_document(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    file_name: str | None = None,
    content: bytes | None = None,
    mime_type: str = "text/csv",
    security_level: str = "PUBLIC",
) -> str:
    """KMS 에 문서 업로드. POST :3001/api/documents (multipart, file 필수).

    기본 확장자는 .csv — KMS 업로드 허용(.pdf/.md/.csv)과 ai-platform
    ingestion 허용(pdf/csv/xlsx/xls)의 교집합(pdf/csv) 중 텍스트 친화 포맷.
    (.txt 는 KMS multer fileFilter 가 거부한다.)

    기본 content 는 호출마다 고유 — KMS 가 동일 내용 재업로드를 409(중복)로
    거부하므로, 고정 기본값이면 두 번째 테스트부터 충돌한다.

    반환: KMS documentId (ai-platform external_id 가 될 값).
    """
    if content is None:
        content = (
            f"DB 손해보험 차량 파손 보상 약관 테스트 문서 {uuid.uuid4().hex}.\n".encode("utf-8")
        )
    name = file_name or f"e2e-{uuid.uuid4().hex[:8]}.csv"
    files = {"file": (name, content, mime_type)}
    data = {"securityLevel": security_level}
    resp = await client.post(
        "/api/documents",
        files=files,
        data=data,
        headers=_auth_headers(jwt),
    )
    resp.raise_for_status()
    body = resp.json()
    # KMS 응답 포맷 호환: {id} 또는 {data:{id}} 또는 {documentId}
    doc_id = (
        body.get("id")
        or body.get("documentId")
        or (body.get("data") or {}).get("id")
    )
    if not doc_id:
        raise AssertionError(f"KMS 업로드 응답에서 documentId 추출 실패: {body!r}")
    return str(doc_id)


async def kms_create_placement(
    client: httpx.AsyncClient,
    jwt: str,
    document_id: str,
    *,
    domain_code: str = DEFAULT_DOMAIN_CODE,
) -> httpx.Response:
    """문서를 도메인에 배치. POST :3001/api/placements (EDITOR 권한 필요).

    배치 성공 → KMS PlacementsService 가 document.updated webhook 을
    domainCodes:[domain_code] 로 ai-platform 에 fire-and-forget 발화한다 (G20 지점).
    """
    payload = {"documentId": document_id, "domainCode": domain_code}
    resp = await client.post(
        "/api/placements",
        json=payload,
        headers=_auth_headers(jwt),
    )
    return resp


# ---------------------------------------------------------------------------
# ai-platform 측 (DB 단언, asyncpg conn)
# ---------------------------------------------------------------------------

async def fetch_aip_documents_by_external_id(conn, external_id: str) -> list[dict]:
    """ai-platform documents 테이블에서 external_id 매칭 행 조회."""
    rows = await conn.fetch(
        "SELECT id, external_id, domain_code, title "
        "FROM documents WHERE external_id = $1 ORDER BY created_at",
        external_id,
    )
    return [dict(r) for r in rows]


async def count_aip_documents_by_external_id(conn, external_id: str) -> int:
    val = await conn.fetchval(
        "SELECT COUNT(*) FROM documents WHERE external_id = $1", external_id
    )
    return int(val or 0)


async def wait_for_sync(
    conn,
    external_id: str,
    *,
    expected_count: int = 1,
    timeout_sec: float = 30.0,
    poll_interval: float = 1.0,
) -> list[dict]:
    """webhook → job_queue → kms_sync 비동기 적재 완료 대기.

    expected_count 행이 보일 때까지 폴링. timeout 시 현재 상태로 반환
    (호출측이 단언 — '안 떴다'도 유효한 결과로 다룰 수 있게).
    """
    deadline = time.time() + timeout_sec
    rows: list[dict] = []
    while time.time() < deadline:
        rows = await fetch_aip_documents_by_external_id(conn, external_id)
        if len(rows) >= expected_count:
            return rows
        await asyncio.sleep(poll_interval)
    return rows


async def assert_no_sync(
    conn,
    external_id: str,
    *,
    settle_sec: float = 8.0,
    poll_interval: float = 1.0,
) -> list[dict]:
    """일정 시간 동안 external_id 행이 '생기지 않음'을 확인 (skip/미동기 단언).

    settle_sec 동안 폴링하며 행이 0건으로 유지되는지 본다.
    행이 생기면 즉시 반환(호출측이 단언 실패 처리).
    """
    deadline = time.time() + settle_sec
    rows: list[dict] = []
    while time.time() < deadline:
        rows = await fetch_aip_documents_by_external_id(conn, external_id)
        if rows:
            return rows
        await asyncio.sleep(poll_interval)
    return rows
