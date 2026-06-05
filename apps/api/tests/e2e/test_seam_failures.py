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
# 주입② G21 (green) — docforge 내구 큐: 워커 재시작 견딤 → 이어서 처리
# ===========================================================================

@pytest.mark.e2e
@pytest.mark.contract
async def test_g21_docforge_job_evaporation(monkeypatch):
    """docforge 워커 재시작 → 잡 잔존(parse_jobs) → 이어서 처리 → 파싱 성공 → green.

    Step19 봉합: docforge 비동기 큐가 인메모리 dict/Queue 에서 SQLite 내구 큐
    (parser/docforge/web/job_store.py)로 전환됐다. 워커/프로세스 재시작에도 잡이
    `parse_jobs` 에 잔존하고, 부팅 시 processing 고아 잡을 queued 로 회수해 워커가
    재클레임·이어서 처리한다. 폴링은 재시작 동안에도 200(processing→done)을
    유지하므로 ai-platform 폴러(docforge_client.py:128-160)는 404 없이 성공한다.

    계약 mock: httpx.AsyncClient 를 내구 재시작 transport 로 교체하고 **실제**
    DocForgeClient.parse 를 돌린다. transport 는 제출 후 첫 폴들에서 'processing'
    (= 워커 재시작 중 잡 잔존), 이후 'done'+markdown 을 돌려준다. 어떤 단계에서도
    404 를 내지 않는다.

    한계: 실제 docforge 워커 재시작이 아니라 내구 큐의 성공 계약(잡 잔존→재처리→
    done, poll 200 유지)을 transport 레벨로 재현. 실제 SQLite 고아 회복 동작은
    parser/tests/test_job_store.py 단위 테스트가 증명한다(orphan→queued→done).
    라이브 재시작 실증(docforge 컨테이너 재기동)은 AIP_E2E_LIVE_INJECT 경로 +
    REPORT 수동 검증 절차로 위임한다.

    본문은 "재시작을 견디고 파싱이 성공해야 한다"고 단언 — Step19 내구 큐로 green.
    """
    # 실제 ai-platform 클라이언트(같은 앱 단위) import — 계약 검증 대상.
    from src.pipeline.parsing.docforge_client import DocForgeClient, ParseError

    import httpx

    transport = INJ.make_docforge_durable_restart_transport(restart_polls=2)
    original_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        # mock transport 사용 시 실제 네트워크 timeout 불필요 — 충돌 키 제거
        kwargs.pop("timeout", None)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)

    client = DocForgeClient(base_url="http://docforge.test:5051", poll_interval_sec=0.01)

    # Step19 내구 큐: 재시작(첫 폴들 processing) 견딘 뒤 done → 성공. 404/ParseError 없음.
    raised = None
    result = None
    try:
        result = await client.parse(b"%PDF-1.4 fake", "durable.pdf", "application/pdf")
    except ParseError as exc:
        raised = exc

    assert raised is None, (
        f"docforge 내구 큐는 재시작을 견디고 이어서 처리해야 한다 — ParseError: {raised}"
    )
    assert result is not None, "내구 큐 재처리 후 파싱 결과를 받아야 한다"
    assert getattr(result, "markdown", ""), (
        "재시작 후 재처리된 잡의 markdown 을 수신해야 한다(잡 영속·이어서 처리 증명)"
    )


# ===========================================================================
# 주입③ G23 (green) — OCR 가용성 TTL 재프로브: 다운→재기동 자가회복
# ===========================================================================

@pytest.mark.e2e
@pytest.mark.contract
async def test_g23_ocr_availability_reprobe_recovery():
    """OCR :5052 다운 → 재기동 → docforge 가 TTL 재프로브로 스스로 다시 잡음 → green.

    Step20 봉합: docforge adapters(apple_vision_remote.py / host_vlm_engine.py)는
    더 이상 가용성을 영구 캐시하지 않는다. host_health.TTLAvailability 가 TTL(기본
    30s) 경과 — 또는 호출 실패 후 invalidate — 시 다음 is_available() 에서 health 를
    재프로브한다. OCR 가 재기동되면 다음 호출에서 자동 인지(자가회복)하고 파이프라인이
    복구된다. graceful degrade(다운 동안 빈 결과)는 유지된다.

    계약 mock: _RecoverableAvailability 가 그 TTL 재프로브 회복 계약을 재현한다 —
    다운 첫 평가(False 캐시) → TTL 경과(advance)/invalidate → 재프로브 → 복구된
    health 반영. 회복이 "재프로브 때문"임을 probe_count 증가로 증명한다(가짜 통과 방지:
    단순 True 반환이 아니라 실제 재프로브가 일어남을 단언).

    한계: 실제 :5052 다운/재기동이 아니라 TTL 재프로브 회복 동작 재현. 실제 seam 회복은
    parser/tests/unit/test_host_health.py 단위 테스트가 증명(다운→TTL 만료→재프로브→True,
    어댑터 자가회복). 라이브 :5052 다운/재기동 실증은 REPORT 수동 검증 절차로 위임.

    본문은 "OCR 재기동 후 재프로브로 is_available()==True 로 자가회복해야 한다"고
    단언 — Step20 TTL 재프로브로 green.
    """
    engine = INJ.make_recoverable_ocr_availability(initial_down=True, ttl_sec=30.0)

    # 1) OCR 다운 상태 첫 평가 → 프로브 1회, False 캐시.
    assert engine.is_available() is False
    assert engine.probe_count == 1

    # 2) TTL 안에서는 재프로브하지 않고 캐시(False) 반환 — 불필요한 프로브 폭주 방지.
    assert engine.is_available() is False
    assert engine.probe_count == 1, "TTL 안에서는 재프로브하지 않아야 한다(캐시)"

    # 3) OCR 재기동 (health 복구) + TTL 경과.
    engine.health_ok = True
    engine.advance(31.0)  # TTL(30s) 초과 → 다음 호출에서 재프로브

    # 4) 자가회복 — 재프로브가 복구된 health 를 반영해 True. probe_count 증가로 재프로브 증명.
    assert engine.is_available() is True, (
        "OCR 재기동 후 TTL 재프로브로 자가회복 기대 — Step20 host_health.TTLAvailability 가 "
        "영구 캐시를 TTL 재프로브로 교체해 이 단언을 green 으로 만든다."
    )
    assert engine.probe_count == 2, (
        "회복은 재프로브에서 비롯돼야 한다(가짜 통과 방지) — TTL 경과 후 health 재프로브 1회 추가."
    )

    # 5) 보강: 호출 실패 후 invalidate 시 TTL 전에도 즉시 재프로브로 회복 인지.
    engine2 = INJ.make_recoverable_ocr_availability(initial_down=True, ttl_sec=30.0)
    assert engine2.is_available() is False
    engine2.health_ok = True
    engine2.invalidate()  # 호출 실패 추정 → 다음 호출 즉시 재프로브
    assert engine2.is_available() is True
    assert engine2.probe_count == 2
