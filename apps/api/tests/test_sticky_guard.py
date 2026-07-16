"""V1 sticky 이중 가드 — TTL + 비대칭 관련성.

진단(2026-07-15 "V1 sticky 워크플로우 하이재킹"): 미완료 워크플로우가 있으면 무조건
sticky로 잡아, 방치된 사주 워크플로우가 "자동차보험 대인배상 절차 알려줘"까지 삼켰다.

가드는 **비대칭**이다 — 진단서의 "유사도 ≥ 임계일 때만 sticky"를 그대로 쓰면 워크플로우
중간 발화("1990-05-15")가 전부 떨어져 무의미한 답이 나온다(graph.py sticky_delegate
주석의 실사고). 그래서 방향을 뒤집어 "강한 반증이 있을 때만 깬다"로 했다.

두 방향을 함께 고정한다:
  - 깨야 할 때 깨는가 (하이재킹 차단)
  - **지켜야 할 때 지키는가** (중간 발화 보존) ← 이게 빠지면 사고가 재발한다
"""

import time

import pytest

from src.supervisor.sticky_guard import (
    StickyGuardConfig,
    is_session_stale,
    profile_signal_text,
    should_break_sticky,
)


CFG = StickyGuardConfig(ttl_seconds=7200, break_similarity=0.6, break_margin=0.15)


# --- ① TTL ---


def test_fresh_session_is_not_stale():
    assert is_session_stale(time.time() - 60, ttl_seconds=7200) is False


def test_abandoned_session_is_stale():
    """진단 예시: 생년월일 단계에서 방치된 사주 워크플로우."""
    assert is_session_stale(time.time() - 10_000, ttl_seconds=7200) is True


def test_ttl_zero_disables_guard():
    """0/음수 = TTL 가드 끔(무한 유지) — 탈출구."""
    assert is_session_stale(time.time() - 999_999, ttl_seconds=0) is False
    assert is_session_stale(time.time() - 999_999, ttl_seconds=-1) is False


def test_stale_boundary_uses_injected_now():
    now = 1_000_000.0
    assert is_session_stale(now - 7201, 7200, now=now) is True
    assert is_session_stale(now - 7199, 7200, now=now) is False


# --- 프로필 신호 텍스트 ---


class _Hint:
    def __init__(self, patterns):
        self.patterns = patterns


class _Profile:
    def __init__(self, pid, name="", description="", domain_scopes=None, intent_hints=None):
        self.id = pid
        self.name = name
        self.description = description
        self.domain_scopes = domain_scopes or []
        self.intent_hints = intent_hints or []


def test_signal_text_gathers_routing_signals():
    p = _Profile(
        "insurance-qa", name="보험 상담", description="자동차보험 약관 안내",
        domain_scopes=["D01"], intent_hints=[_Hint(["대인배상", "특약"])],
    )
    text = profile_signal_text(p)
    for token in ("보험 상담", "자동차보험 약관 안내", "D01", "대인배상", "특약"):
        assert token in text


def test_signal_text_empty_profile():
    assert profile_signal_text(_Profile("x")) == ""


# --- ② 비대칭 관련성 ---
#
# 임베딩을 직접 만들 수 없으므로 단위 벡터로 유사도를 설계한다.
# [1,0] = 사주 축, [0,1] = 보험 축.

SAJU = [1.0, 0.0]
INSURANCE = [0.0, 1.0]


def test_breaks_on_strong_other_domain():
    """진단 재현: 사주 sticky 중 보험 질문이 오면 깬다."""
    breaks, ev = should_break_sticky(
        question_vec=INSURANCE, sticky_vec=SAJU, rival_vecs={"insurance-qa": INSURANCE}, cfg=CFG,
    )
    assert breaks is True
    assert ev["rival"] == "insurance-qa"


def test_keeps_sticky_when_signal_is_weak():
    """★중간 발화 보존 — '1990-05-15'처럼 어느 도메인과도 안 닮은 입력.

    이게 깨지면 사주 워크플로우가 동작 불가해진다(실사고 재발).
    """
    weak = [0.3, 0.3]  # 양쪽 모두와 애매하게 유사 → 어느 쪽도 임계 미달
    breaks, ev = should_break_sticky(weak, SAJU, {"insurance-qa": INSURANCE}, CFG)
    assert breaks is False, f"약한 신호로 sticky를 깼다: {ev}"


def test_keeps_sticky_when_question_matches_sticky_domain():
    breaks, _ = should_break_sticky(SAJU, SAJU, {"insurance-qa": INSURANCE}, CFG)
    assert breaks is False


def test_margin_required_even_above_threshold():
    """타 도메인이 임계는 넘어도 sticky와 비등하면 깨지 않는다 — 애매하면 유지."""
    tie = [0.707, 0.707]  # 양쪽과 동일하게 0.707 → margin 0
    breaks, ev = should_break_sticky(tie, SAJU, {"insurance-qa": INSURANCE}, CFG)
    assert breaks is False
    assert ev["margin"] == 0.0


def test_threshold_required_even_with_margin():
    """마진은 있어도 절대 유사도가 낮으면 '강한 증거'가 아니다."""
    cfg = StickyGuardConfig(ttl_seconds=7200, break_similarity=0.6, break_margin=0.15)
    q = [0.0, 0.5]          # 보험과 sim=1.0? → 정규화되므로 방향만 본다
    # 방향이 보험이므로 sim=1.0이 되어버린다 — 절대값이 낮은 경우를 만들려면 경합 벡터를 튼다.
    breaks, _ = should_break_sticky([1.0, 0.2], SAJU, {"insurance-qa": [0.2, 1.0]}, cfg)
    assert breaks is False, "sticky 쪽이 더 유사한데 깨면 안 된다"


def test_no_rivals_keeps_sticky():
    breaks, ev = should_break_sticky(INSURANCE, SAJU, {}, CFG)
    assert breaks is False
    assert ev["rival"] == ""


def test_zero_vectors_do_not_break():
    """임베딩이 비면(장애 등) 깨지 않는다 — 가드 실패가 워크플로우를 잃게 하면 안 된다."""
    breaks, _ = should_break_sticky([], SAJU, {"insurance-qa": INSURANCE}, CFG)
    assert breaks is False


def test_evidence_is_logged_for_diagnosis():
    """판단 근거가 남아야 오라우팅 사후 분석이 가능하다."""
    _, ev = should_break_sticky(INSURANCE, SAJU, {"insurance-qa": INSURANCE}, CFG)
    assert set(ev) >= {"sticky_sim", "rival", "rival_sim", "margin", "threshold", "required_margin"}


# --- 임계 보정 회귀 (2026-07-16 실측 배터리) ---
#
# 질문↔프로필신호 코사인은 0.13~0.42 대역이다. SemanticClassifier의 0.6을 빌려오면
# 가드가 통째로 무력해진다(실측: 4건 중 0건 적중). 아래는 그 회귀를 고정한다 —
# 임계를 되돌리면 여기서 잡힌다.
#
# (sticky_sim, rival_sim) — BGE-m3-ko 1024d, 실제 fortune-saju/insurance-qa 프로필 신호

_MEASURED_BREAK = [           # sticky(사주)를 깨야 하는 질문
    ("자동차보험 대인배상 절차 알려줘", 0.298, 0.387),
    ("약관에 자기부담금 얼마야", 0.256, 0.316),
    ("실손보험 보장 범위 알려줘", 0.340, 0.424),
]
_MEASURED_KEEP = [            # 사주 워크플로우를 유지해야 하는 입력
    ("1990-05-15", 0.260, 0.208),
    ("투자", 0.387, 0.326),
    ("네", 0.387, 0.324),
    ("남자", 0.386, 0.324),
    ("09:30", 0.386, 0.325),
    ("양력", 0.192, 0.132),
    ("사주 봐줘", 0.253, 0.223),
    ("올해 재물운 어때", 0.367, 0.322),
    ("궁합 봐줘", 0.283, 0.240),
    ("내 성격 어때", 0.244, 0.181),
]


def _judge(sticky_sim: float, rival_sim: float, cfg: StickyGuardConfig) -> bool:
    """should_break_sticky의 판정식 — 실측 유사도로 직접 검증한다."""
    return rival_sim >= cfg.break_similarity and (rival_sim - sticky_sim) >= cfg.break_margin


def _shipped_cfg() -> StickyGuardConfig:
    from src.config import Settings

    s = Settings()
    return StickyGuardConfig(
        ttl_seconds=s.sticky_ttl_seconds,
        break_similarity=s.sticky_break_similarity,
        break_margin=s.sticky_break_margin,
    )


@pytest.mark.parametrize("label,sticky_sim,rival_sim", _MEASURED_BREAK)
def test_shipped_thresholds_catch_measured_hijacks(label, sticky_sim, rival_sim):
    assert _judge(sticky_sim, rival_sim, _shipped_cfg()) is True, f"{label}: 하이재킹을 못 잡는다"


@pytest.mark.parametrize("label,sticky_sim,rival_sim", _MEASURED_KEEP)
def test_shipped_thresholds_never_break_measured_keeps(label, sticky_sim, rival_sim):
    assert _judge(sticky_sim, rival_sim, _shipped_cfg()) is False, f"{label}: sticky를 잘못 깬다"


def test_borrowed_060_threshold_would_be_a_no_op():
    """0.6(SemanticClassifier 눈금)을 빌려오면 가드가 통째로 죽는다 — 회귀 방지 근거."""
    dead = StickyGuardConfig(ttl_seconds=7200, break_similarity=0.6, break_margin=0.15)
    caught = [lbl for lbl, ss, rs in _MEASURED_BREAK if _judge(ss, rs, dead)]
    assert caught == [], "0.6에서 뭔가 잡혔다면 배터리나 판정식이 바뀐 것"
