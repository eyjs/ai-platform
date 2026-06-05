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

    ⚠️ Step18 이전(인메모리 큐) 결함 계약. Step19 에서 docforge 가 SQLite 내구
    큐로 전환되어 G21 이 green 으로 봉합되었다 — 현재 G21 테스트는 아래
    ``make_docforge_durable_restart_transport`` 를 사용한다. 본 함수는 회귀/이력
    참고용으로 보존.
    """
    return _MockTransport().transport


# ---------------------------------------------------------------------------
# 주입② G21 (green) — docforge 내구 큐: 워커 재시작 견딤 계약 mock
# ---------------------------------------------------------------------------
#
# Step19 봉합 후 실제 코드(parser/docforge/web/v1_routes.py + job_store.py):
#   잡이 SQLite parse_jobs 에 INSERT 되어 워커/프로세스 재시작에도 잔존.
#   부팅 시 recover_orphans 가 processing 고아 잡을 queued 로 회수 → 워커가
#   재클레임해 이어서 처리. 폴링은 재시작 동안에도 200(queued/processing/done)
#   을 유지하며 404 를 내지 않는다 (v1_routes.py parse_async_status).
# ai-platform 폴러(docforge_client.py:128-160):
#   200 + status in (queued/processing) → 계속 폴링, status==done → 결과 반환.
#   404 가 없으므로 ParseError 없이 성공.


class _DurableRestartTransport:
    """워커 재시작을 견디는 docforge 내구 큐 계약을 재현하는 transport.

    submit → job_id. 폴링: 처음 ``restart_polls`` 회는 200 'processing'
    (워커 재시작 중 — 잡은 parse_jobs 에 잔존, 404 아님) → 이후 200 'done' +
    markdown. 어떤 단계에서도 404 를 내지 않는다(잡 영속).

    한계: 실제 docforge 워커 재시작이 아니라 내구 큐의 *성공 계약*(잡 잔존 →
    재처리 → done, poll 200 유지)을 transport 레벨로 재현. 실제 재시작 회복은
    parser/tests/test_job_store.py 의 단위 테스트가 증명한다(SQLite 고아 회복).
    """

    def __init__(self, restart_polls: int = 2):
        import httpx

        state = {"polls": 0}

        def _handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/v1/parse/async"):
                # 제출 성공 — 잡이 내구 스토어에 INSERT 됨.
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": {"job_id": "durable-job", "status": "queued",
                                 "queue_size": 1},
                    },
                )
            # 폴링 — 잡은 항상 잔존(200). 재시작 동안 processing, 회복 후 done.
            state["polls"] += 1
            if state["polls"] <= restart_polls:
                return httpx.Response(
                    200,
                    json={"success": True, "data": {"status": "processing"}},
                )
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "status": "done",
                        "markdown": "# parsed (durable)\n\nrecovered after restart",
                        "metadata": {},
                        "stats": {},
                    },
                },
            )

        self._transport = httpx.MockTransport(_handler)

    @property
    def transport(self):
        return self._transport


def make_docforge_durable_restart_transport(restart_polls: int = 2):
    """docforge SQLite 내구 큐(Step19)의 재시작 견딤 계약을 재현하는 transport.

    한계: 실제 워커 재시작이 아니라 내구 큐의 성공 계약(잡 잔존→재처리→done,
    poll 200 유지)을 재현. 실제 SQLite 고아 회복은 parser 단위 테스트가 증명.
    """
    return _DurableRestartTransport(restart_polls=restart_polls).transport


# ---------------------------------------------------------------------------
# 주입③ G23 (green) — OCR 가용성 TTL 재프로브 자가회복 계약 mock
# ---------------------------------------------------------------------------
#
# Step20 봉합: 실제 코드(parser/docforge/adapters/apple_vision_remote.py +
#   host_health.TTLAvailability)는 더 이상 _available 를 영구 캐시하지 않는다.
#   TTL(기본 30s) 경과 시 — 또는 호출 실패 후 invalidate 시 — 다음 is_available()
#   에서 health 를 재프로브한다. OCR 가 재기동되면 docforge 가 스스로 다시 잡는다.
#
# 계약 mock 은 그 TTL 재프로브 회복 동작을 재현한다 (실제 :5052 다운/재기동이 아님).
# 회복이 "재프로브 때문"임을 probe 호출 횟수로 증명한다 (가짜 통과 방지).


@dataclass
class _RecoverableAvailability:
    """is_available() TTL 재프로브 자가회복을 재현하는 최소 모델.

    docforge host_health.TTLAvailability 와 동일 계약:
      - 첫 평가에서 health 를 프로브해 캐시
      - TTL 안에서는 캐시 반환(프로브 안 함)
      - TTL 경과(advance) 또는 invalidate 후엔 health 를 다시 프로브 → 현재 health 반영
      - 따라서 다운 후 health 가 복구되면 재프로브에서 True 로 자가회복
    """

    health_ok: bool
    ttl_sec: float = 30.0
    _cached: bool | None = None
    _age: float = 0.0  # 마지막 프로브 이후 가상 경과 시간(monotonic 대체)
    probe_count: int = 0  # 실제 health 프로브 횟수 — 재프로브 회복 증명용

    def _probe(self) -> bool:
        self.probe_count += 1
        self._cached = bool(self.health_ok)
        self._age = 0.0
        return self._cached

    def is_available(self) -> bool:
        if self._cached is None or self._age >= self.ttl_sec:
            return self._probe()
        return self._cached

    def advance(self, seconds: float) -> None:
        """가상 시간 경과 — TTL 만료를 시뮬레이션해 다음 호출에서 재프로브하게 한다."""
        self._age += seconds

    def invalidate(self) -> None:
        """다음 is_available() 을 즉시 재프로브하게 한다(호출 실패 후 빠른 회복)."""
        self._age = self.ttl_sec


def make_recoverable_ocr_availability(initial_down: bool = True, ttl_sec: float = 30.0):
    """OCR TTL 재프로브 자가회복 계약 재현 객체.

    한계: 실제 :5052 다운/재기동이 아니라 TTL 재프로브 회복 동작 재현.
    실제 seam 은 docforge host_health.TTLAvailability 이며, 여기선 그 회복 계약
    (다운 캐시 → TTL 경과/invalidate → 재프로브 → 복구된 health 반영)을 모사한다.
    회복이 재프로브에서 비롯됨을 probe_count 로 증명한다.
    """
    return _RecoverableAvailability(health_ok=not initial_down, ttl_sec=ttl_sec)
