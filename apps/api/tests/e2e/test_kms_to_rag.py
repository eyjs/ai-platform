"""Step 17a — 3-서비스 골든패스 E2E (G22).

핸드오프 §6 시나리오 D 코드화:
  1. KMS 업로드  POST :3001/api/documents
  2. 배치        POST :3001/api/placements (domainCode=DB-DAMAGE)
  3. ai-platform documents 테이블에 external_id=KMS id · domain_code=DB-DAMAGE 단일행

추가 단언 (임베딩·배치 분리 설계):
  - document.created(배치 전) → holding 도메인(__unplaced__)으로 즉시 적재
    / document.updated(배치 후) → 재임베딩 없이 domain_code 만 재태깅
  - 멱등: 동일 배치 재전송 → 행 수 불변 (Step 25 (external_id, domain_code) UPSERT)

게이트: AIP_E2E_LIVE 미설정 시 conftest 가 전체 skip (조용한 통과 아님).
프로덕션 코드 무변 — 전부 HTTP/DB 경유.
"""

from __future__ import annotations

import pytest

from . import _harness as H


@pytest.mark.e2e
@pytest.mark.live
async def test_golden_path_single_row(
    kms_client, kms_jwt, aip_db, cleanup_external_ids
):
    """업로드 → 배치(DB-DAMAGE) → ai-platform documents 단일행."""
    # 1) KMS 업로드
    kms_doc_id = await H.kms_upload_document(kms_client, kms_jwt)
    cleanup_external_ids(kms_doc_id)

    # 2) 배치 — document.updated(domainCodes=[DB-DAMAGE]) fire-and-forget 트리거
    resp = await H.kms_create_placement(
        kms_client, kms_jwt, kms_doc_id, domain_code=H.DEFAULT_DOMAIN_CODE
    )
    assert resp.status_code in (200, 201), f"배치 실패: {resp.status_code} {resp.text}"

    # 3) ai-platform 적재 대기 + 단일행 단언
    # 90s = docforge 일시 장애 시 job_queue 재시도 1사이클(딜레이 30s) + 처리 여유.
    # 30s 는 정상경로만 커버해 라이브에서 재시도 한 번에도 플레이키했다.
    rows = await H.wait_for_sync(
        aip_db, kms_doc_id, expected_count=1, timeout_sec=90.0
    )
    assert len(rows) == 1, (
        f"external_id={kms_doc_id} 단일행 기대, 실제 {len(rows)}건: {rows}"
    )
    assert rows[0]["external_id"] == kms_doc_id
    assert rows[0]["domain_code"] == H.DEFAULT_DOMAIN_CODE


@pytest.mark.e2e
@pytest.mark.live
async def test_created_embeds_unplaced_then_placement_retags(
    kms_client, kms_jwt, aip_db, cleanup_external_ids
):
    """document.created(배치 전) → holding 적재 / document.updated(배치 후) → 재태깅.

    임베딩·배치 분리 설계: 업로드 즉시 파싱·청킹·임베딩하되 아직 도메인이 없으므로
    holding 도메인(__unplaced__)으로 적재된다("업로드=청크 생성" 보장, 단 검색 스코프
    밖이라 비노출). 배치가 오면 **재임베딩 없이** 같은 행의 domain_code 만 상품도메인으로
    in-place 재태깅된다(행 수 불변).
    """
    kms_doc_id = await H.kms_upload_document(kms_client, kms_jwt)
    cleanup_external_ids(kms_doc_id)

    # 배치 전: created → 즉시 임베딩, 단 holding 도메인(__unplaced__)
    pre = await H.wait_for_sync(aip_db, kms_doc_id, expected_count=1, timeout_sec=90.0)
    assert len(pre) == 1, f"업로드 후 단일 적재 기대, 실제 {len(pre)}건: {pre}"
    assert pre[0]["domain_code"] == H.UNPLACED_DOMAIN, (
        f"배치 전에는 holding 도메인(__unplaced__) 기대, 실제 {pre[0]['domain_code']}"
    )

    # 배치 후: updated → 재임베딩 없이 domain_code 만 상품도메인으로 재태깅 (행 수 불변)
    resp = await H.kms_create_placement(kms_client, kms_jwt, kms_doc_id)
    assert resp.status_code in (200, 201), f"배치 실패: {resp.status_code} {resp.text}"

    post = await H.wait_for_domain(
        aip_db, kms_doc_id, H.DEFAULT_DOMAIN_CODE, timeout_sec=90.0
    )
    assert len(post) == 1, f"재태깅 후 단일행 기대, 실제 {len(post)}건: {post}"
    assert post[0]["domain_code"] == H.DEFAULT_DOMAIN_CODE


@pytest.mark.e2e
@pytest.mark.live
async def test_idempotent_replacement_row_count_stable(
    kms_client, kms_jwt, aip_db, cleanup_external_ids
):
    """동일 배치 재전송 → 행 수 불변 (Step 25 멱등 UPSERT)."""
    kms_doc_id = await H.kms_upload_document(kms_client, kms_jwt)
    cleanup_external_ids(kms_doc_id)

    # 1차 배치
    r1 = await H.kms_create_placement(kms_client, kms_jwt, kms_doc_id)
    assert r1.status_code in (200, 201, 409), f"1차 배치: {r1.status_code} {r1.text}"
    rows1 = await H.wait_for_sync(aip_db, kms_doc_id, expected_count=1, timeout_sec=90.0)
    assert len(rows1) == 1, f"1차 적재 단일행 기대, 실제 {len(rows1)}: {rows1}"

    # 2차 배치(동일) — 이미 배치돼 있으면 KMS 409 가능. 어느 쪽이든
    # ai-platform documents 행 수는 불변이어야 한다 (멱등).
    await H.kms_create_placement(kms_client, kms_jwt, kms_doc_id)
    count2 = await H.count_aip_documents_by_external_id(aip_db, kms_doc_id)
    assert count2 == 1, (
        f"멱등 위반: 재배치 후 행 수 {count2} (1 기대). "
        "(external_id, domain_code) UPSERT 가 중복 적재를 막아야 한다."
    )
