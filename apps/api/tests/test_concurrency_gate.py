"""전역 동시 실행 게이트 단위 테스트.

배경(아키텍처 진단 2026-07-15): max_concurrent_agents가 Settings에 있으나 소비처가
없어 실제 상한이 없었다("용량 절벽붕괴"). 설정이 그 사실을 가리고 있었다 = 죽은 설정.

슬롯 누수가 이 게이트의 최대 위험이다 — 해제를 한 번 빠뜨리면 상한이 조용히 줄어들어
결국 모든 요청이 503이 된다. 정상 경로뿐 아니라 예외·조기반환 경로도 함께 고정한다.
"""

import pytest

from src.gateway.concurrency_gate import RETRY_AFTER_SECONDS, ConcurrencyGate


# --- 기본 동작 ---


def test_acquires_up_to_limit():
    gate = ConcurrencyGate(limit=3)
    assert [gate.try_acquire() for _ in range(3)] == [True, True, True]
    assert gate.active == 3


def test_rejects_beyond_limit():
    gate = ConcurrencyGate(limit=2)
    gate.try_acquire()
    gate.try_acquire()
    assert gate.try_acquire() is False
    assert gate.active == 2, "거부는 슬롯을 늘리지 않아야 한다"


def test_release_frees_slot():
    gate = ConcurrencyGate(limit=1)
    assert gate.try_acquire() is True
    assert gate.try_acquire() is False
    gate.release()
    assert gate.try_acquire() is True


def test_rejected_counter_tracks_pressure():
    """상한이 실제로 물리는지 보는 관측점 — /api/health에 노출된다."""
    gate = ConcurrencyGate(limit=1)
    gate.try_acquire()
    gate.try_acquire()
    gate.try_acquire()
    assert gate.rejected == 2


# --- 무제한 탈출구 ---


@pytest.mark.parametrize("limit", [0, -1])
def test_zero_or_negative_limit_is_unlimited(limit):
    """게이트를 끄는 탈출구 — 단일 사용자 개발 환경 등."""
    gate = ConcurrencyGate(limit=limit)
    assert all(gate.try_acquire() for _ in range(100))
    assert gate.rejected == 0


def test_unlimited_release_is_noop():
    gate = ConcurrencyGate(limit=0)
    gate.try_acquire()
    gate.release()
    gate.release()  # 과잉 해제도 안전해야 한다
    assert gate.active == 0


# --- 누수/과잉 해제 방어 ---


def test_release_never_goes_negative():
    """과잉 해제가 음수로 새면 상한이 사실상 늘어난다 — 반대 방향 사고."""
    gate = ConcurrencyGate(limit=2)
    gate.try_acquire()
    gate.release()
    gate.release()
    gate.release()
    assert gate.active == 0
    assert gate.try_acquire() is True
    assert gate.try_acquire() is True
    assert gate.try_acquire() is False, "상한이 늘어나면 안 된다"


def test_acquire_release_cycle_does_not_leak():
    """반복 사용 후에도 상한이 그대로여야 한다 — 누수가 있으면 여기서 드러난다."""
    gate = ConcurrencyGate(limit=2)
    for _ in range(50):
        assert gate.try_acquire() is True
        gate.release()
    assert gate.active == 0
    assert gate.try_acquire() and gate.try_acquire()
    assert gate.try_acquire() is False


# --- health 노출 ---


def test_snapshot_shape():
    gate = ConcurrencyGate(limit=5)
    gate.try_acquire()
    assert gate.snapshot() == {"limit": 5, "active": 1, "rejected_total": 0}


def test_retry_after_is_positive():
    """503에 실을 값 — 0이면 클라이언트가 즉시 재시도해 폭주를 키운다."""
    assert RETRY_AFTER_SECONDS > 0
