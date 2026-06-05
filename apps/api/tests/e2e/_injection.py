"""실패주입 레이어 (P3 Step 17b).

seam 결함(G20/G21/G23)을 가시화하기 위한 주입 헬퍼.
라이브 우선(실제 5xx·재시작·다운 주입), 라이브 불가 시 **계약 수준 mock** 으로
seam 의 실패 계약을 재현한다. 계약 mock 은 '실제 seam 검증이 아님'을 docstring 에 명시한다.

프로덕션 코드 무변 — 주입은 HTTP 응답/클라이언트 동작 수준에서만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# 주입② G21 — docforge 잡 증발(404) 계약 mock
# ---------------------------------------------------------------------------
#
# 실제 코드(parser/docforge/web/v1_routes.py:411 parse_async_status):
#   _async_jobs 는 인메모리 dict. 워커 재시작 시 job 증발 →
#   _async_jobs.get(job_id) is None → HTTP 404 ("작업을 찾을 수 없습니다").
# ai-platform 폴러(src/pipeline/parsing/docforge_client.py:138):
#   poll 응답 404 → raise ParseError("DocForge 작업이 만료/소실되었습니다").
#   재큐/재시도 없음 → 영구 실패.
#
# 계약 mock 은 이 404 → ParseError 계약을 transport 레벨에서 재현한다.
# (실제 워커 재시작을 일으키는 라이브 검증이 아님.)


class _MockTransport:
    """httpx MockTransport 래퍼 — submit 은 정상, poll 은 404 를 돌려준다.

    docforge 워커가 job 제출 직후 재시작되어 인메모리 job 이 증발한 상황을
    transport 레벨에서 재현. ai-platform DocForgeClient 가 이 transport 를 쓰면
    poll 단계에서 404 를 받아 ParseError 로 끝나야 한다 (= 현재 결함, xfail).
    """

    def __init__(self):
        import httpx

        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/v1/parse/async"):
                # 제출은 성공 — job_id 즉시 반환 (워커는 아직 살아 있음)
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {"job_id": "evaporated-job", "status": "queued",
                                 "queue_size": 1},
                    },
                )
            # 폴링 — 워커 재시작으로 job 증발 → 404 (v1_routes.py:420 계약)
            return httpx.Response(
                404,
                json={
                    "success": False,
                    "error": {"code": "NOT_FOUND",
                              "message": "작업을 찾을 수 없습니다 (만료되었거나 잘못된 ID)."},
                },
            )

        self._transport = httpx.MockTransport(_handler)

    @property
    def transport(self):
        return self._transport


def make_docforge_evaporation_transport():
    """docforge 워커 재시작(잡 증발) 계약을 재현하는 httpx MockTransport.

    한계: 실제 docforge 워커 재시작이 아니라 transport 404 재현.
    실제 seam 의 인메모리 큐 증발 계약(v1_routes.py:294/420)을 검증한다.
    """
    return _MockTransport().transport


# ---------------------------------------------------------------------------
# 주입③ G23 — OCR 가용성 캐시 영구 False 계약 mock
# ---------------------------------------------------------------------------
#
# 실제 코드(parser/docforge/adapters/apple_vision_remote.py:37 is_available):
#   self._available 를 1회 평가 후 캐시. 다운 시 :48 False 세팅 →
#   :38-39 에서 TTL/리셋 없이 영구 False 반환. OCR 복구돼도 미반영.
#
# 계약 mock 은 동일한 캐시 고착 동작을 재현한다 (실제 :5052 다운/재기동이 아님).


@dataclass
class _StickyAvailability:
    """is_available() 캐시 고착을 재현하는 최소 모델.

    apple_vision_remote.AppleVisionRemoteEngine 와 동일 계약:
      - 첫 평가에서 health 가 다운이면 False 캐시
      - 이후 health 가 복구돼도 캐시된 False 를 영구 반환
    """

    health_ok: bool
    _cached: bool | None = None

    def is_available(self) -> bool:
        if self._cached is not None:
            return self._cached
        self._cached = bool(self.health_ok)
        return self._cached


def make_sticky_ocr_availability(initial_down: bool = True):
    """OCR 캐시 고착 계약 재현 객체.

    한계: 실제 :5052 다운/재기동이 아니라 캐시 고착 동작 재현.
    실제 seam(apple_vision_remote.py:37-50)의 영구 False 계약을 검증한다.
    """
    return _StickyAvailability(health_ok=not initial_down)
