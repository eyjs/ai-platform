"""Step 17b — 실패주입 3종 (G20/G21/G23).

세 주입 모두 **현재 코드에서 실제로 실패함**을 xfail(strict=True) 로 가시화한다.
strict 이므로 우연히 통과(XPASS)하면 테스트가 실패한다 — 즉 "실제 빨강"을 강제.
Step 18(Outbox)/19(PG큐)/20(호스트 회복)에서 각각 green 전환 예정.

게이트: 라이브 주입은 conftest 의 AIP_E2E_LIVE + 헬스 프리체크를 따른다 (조용한 통과 금지).
계약 mock 폴백은 라이브 의존 없이 seam 의 실패 계약을 재현하며, 그 한계를 명시한다.

프로덕션 코드 무변 — 주입은 HTTP/클라이언트 동작 레벨에서만.
"""

from __future__ import annotations

import os

import pytest

from . import _harness as H
from . import _injection as INJ

#: 라이브 주입(실제 5xx/재시작/다운)을 시도할지. 미설정이면 계약 mock 폴백.
_LIVE_INJECT = os.environ.get("AIP_E2E_LIVE_INJECT", "") == "1"


# ===========================================================================
# 주입① G20 — webhook fire-and-forget (배치 커밋, RAG 미동기)
# ===========================================================================

@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.xfail(
    strict=True,
    reason=(
        "G20: KMS placements.service.ts:357 dispatchPlacementSync 가 fire-and-forget. "
        "webhook 5xx 시 배치는 커밋됐는데 ai-platform RAG 미동기 — 재시도/Outbox 없음. "
        "현재 코드는 실패해야 정상(Step18 Outbox 에서 green 전환)."
    ),
)
async def test_g20_webhook_fire_and_forget(
    kms_client, kms_jwt, aip_db, cleanup_external_ids
):
    """webhook 수신부 5xx 강제 → 배치 성공인데 documents 행 미생성 → xfail.

    라이브: ai-platform /webhooks/kms 를 5xx 로 만들거나 webhook secret 을 무력화한 뒤
    배치를 수행한다. 배치는 KMS 에서 커밋되지만(fire-and-forget) RAG 동기화는 유실된다.
    이 테스트의 본문 단언(=동기화 성공 기대)은 현재 코드에서 실패해야 한다(xfail).
    """
    if not _LIVE_INJECT:
        pytest.skip(
            "AIP_E2E_LIVE_INJECT 미설정 — webhook 5xx 라이브 주입 생략 "
            "(계약 mock 부적합: fire-and-forget 유실은 라이브 주입으로만 충실히 검증). "
            "(조용한 통과 아님: 명시적 skip)"
        )

    kms_doc_id = await H.kms_upload_document(kms_client, kms_jwt)
    cleanup_external_ids(kms_doc_id)

    # webhook 수신부를 5xx 로 강제 (인프라 주입 — 환경별 구현).
    INJ_marker = os.environ.get("AIP_E2E_WEBHOOK_FORCE_5XX", "")
    if not INJ_marker:
        pytest.skip(
            "AIP_E2E_WEBHOOK_FORCE_5XX 미설정 — webhook 5xx 주입 메커니즘 부재. "
            "(조용한 통과 아님: 명시적 skip)"
        )

    resp = await H.kms_create_placement(kms_client, kms_jwt, kms_doc_id)
    assert resp.status_code in (200, 201), "배치는 fire-and-forget 라 커밋되어야 한다"

    # 동기화 기대 — 현재 코드는 5xx 를 삼키고 재시도 없음 → 행 안 뜸 → 이 단언이 실패(xfail)
    rows = await H.wait_for_sync(aip_db, kms_doc_id, expected_count=1, timeout_sec=20.0)
    assert len(rows) == 1, (
        f"webhook 5xx 후에도 RAG 동기화 기대 — 현재 코드는 유실(행 {len(rows)}). "
        "Step18 Outbox 가 이 단언을 green 으로 만든다."
    )


# ===========================================================================
# 주입② G21 — docforge 잡 증발(404) → ParseError, 재큐 없음
# ===========================================================================

@pytest.mark.e2e
@pytest.mark.xfail(
    strict=True,
    reason=(
        "G21: docforge v1_routes.py:294 _async_jobs 인메모리. 워커 재시작 시 job 증발 → "
        "polling 404(:420) → ai-platform docforge_client.py:138 ParseError, 재큐 없음. "
        "현재 코드는 실패해야 정상(Step19 PG큐에서 green 전환)."
    ),
)
async def test_g21_docforge_job_evaporation(monkeypatch):
    """docforge 워커 재시작(잡 증발) → poll 404 → ParseError → 영구 실패 → xfail.

    계약 mock: httpx.AsyncClient 를 잡 증발 transport 로 교체하고 **실제** DocForgeClient.parse
    를 돌린다. 실제 폴링 로직(docforge_client.py:128-141)이 404 를 받아 ParseError 를 던지는지
    검증한다.

    한계: 실제 docforge 워커 재시작이 아니라 transport 404 재현. 인메모리 큐 증발(v1_routes.py:294)
    의 *실패 계약*을 검증하며, 라이브 재시작 검증은 AIP_E2E_LIVE_INJECT 경로 별도.

    본문은 "파싱이 성공해야 한다"고 단언 — 현재 코드는 404 로 영구 실패하므로 이 단언이 실패(xfail).
    """
    # 실제 ai-platform 클라이언트(같은 앱 단위) import — 계약 검증 대상.
    from src.pipeline.parsing.docforge_client import DocForgeClient, ParseError

    import httpx

    transport = INJ.make_docforge_evaporation_transport()
    original_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        # mock transport 사용 시 실제 네트워크 timeout 불필요 — 충돌 키 제거
        kwargs.pop("timeout", None)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)

    client = DocForgeClient(base_url="http://docforge.test:5051", poll_interval_sec=0.01)

    # 현재 코드: poll 404 → ParseError("만료/소실"). 재큐 없음.
    # 우리는 "파싱 성공"을 기대 단언 → 실제로는 ParseError → 이 테스트 본문이 실패(xfail).
    raised = None
    try:
        await client.parse(b"%PDF-1.4 fake", "evap.pdf", "application/pdf")
    except ParseError as exc:
        raised = exc

    # 결함 가시화: 현재 코드는 ParseError 로 끝남(재큐/재시도 없음).
    # green 전환(Step19) 후엔 재큐로 성공해 raised is None 이어야 한다.
    assert raised is None, (
        f"docforge 잡 증발(404) 후 재큐 없이 영구 실패: {raised}. "
        "Step19 PG큐가 이 단언을 green 으로 만든다."
    )


# ===========================================================================
# 주입③ G23 — OCR 가용성 캐시 영구 False → 재기동 후 자동복구 안 됨
# ===========================================================================

@pytest.mark.e2e
@pytest.mark.xfail(
    strict=True,
    reason=(
        "G23: docforge adapters/apple_vision_remote.py:37 is_available 가 self._available 캐시. "
        "다운 시 :48 False → :38-39 TTL/리셋 없이 영구 False. OCR 복구돼도 미반영. "
        "현재 코드는 실패해야 정상(Step20 호스트 회복에서 green 전환)."
    ),
)
async def test_g23_ocr_availability_cache_sticky():
    """OCR :5052 다운 → 캐시 False 고착 → 재기동 후에도 자동복구 안 됨 → xfail.

    계약 mock: apple_vision_remote.AppleVisionRemoteEngine.is_available 의 캐시 고착 계약을
    재현(_StickyAvailability). 첫 평가에서 다운(False) → 이후 health 복구돼도 False 유지.

    한계: 실제 :5052 다운/재기동이 아니라 캐시 고착 동작 재현. 실제 seam(apple_vision_remote.py:37-50)
    의 영구 False 계약을 검증.

    본문은 "OCR 복구 후 is_available()==True 여야 한다"고 단언 — 현재 코드는 캐시 고착으로 False →
    이 단언이 실패(xfail).
    """
    engine = INJ.make_sticky_ocr_availability(initial_down=True)

    # 1) 다운 상태 첫 평가 → False 캐시
    assert engine.is_available() is False

    # 2) OCR 복구 (health 가 다시 ok). 현재 코드는 캐시를 리셋하지 않는다.
    engine.health_ok = True

    # 3) 자동복구 기대 — 현재 코드는 캐시 고착으로 여전히 False → 이 단언이 실패(xfail).
    assert engine.is_available() is True, (
        "OCR 복구 후 자동 재감지 기대 — 현재 코드는 _available 캐시 영구 False. "
        "Step20 호스트 회복(캐시 TTL/리셋)이 이 단언을 green 으로 만든다."
    )
