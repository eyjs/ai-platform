"""sticky 워크플로우 이중 가드 — 방치(TTL) + 하이재킹(비대칭 관련성) 차단.

배경(아키텍처 진단 2026-07-15 "V1 sticky 워크플로우 하이재킹"): detect_sticky는
미완료 워크플로우 세션이 있으면 무조건 그 프로필로 잡고 decompose를 통째 우회했다.
세션 나이도 도메인 관련성도 보지 않아, 생년월일 단계에서 방치된 사주 워크플로우가
"자동차보험 대인배상 절차 알려줘"까지 삼켰다.

**왜 비대칭인가** — 진단서는 "새 질문↔워크플로우 도메인 유사도가 임계 이상일 때만
sticky"라고 적었지만, 그대로 하면 알려진 실사고가 재발한다. 워크플로우 중간 발화는
"1990-05-15", "투자", "네" 같은 것들이고 도메인 신호가 거의 없어 유사도가 낮은 게
정상이다. 유사도로 sticky를 "허가"하면 이런 정상 답변이 전부 decompose로 새고,
단독 질문으로 재해석돼 무의미한 답이 나온다(graph.py sticky_delegate 주석의 실사고 —
그 경로는 폴백조차 막아둘 만큼 확실한 사고였다).

그래서 방향을 뒤집었다:
  기본은 sticky 유지. **다른 도메인이라는 강한 증거가 있을 때만** 깬다.
  - 증거 = 타 프로필이 임계 이상으로 매칭 AND sticky 프로필보다 마진만큼 앞섬
  - 신호가 약하면(대부분의 중간 발화) 유지 — 워크플로우 보존이 기본값

로컬 LLM 원칙과도 정합한다: 판단을 LLM에 묻지 않고 임베딩 신호와 결정적 임계로 푼다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class StickyGuardConfig:
    ttl_seconds: int
    break_similarity: float
    break_margin: float


def is_session_stale(started_at: float, ttl_seconds: int, *, now: float | None = None) -> bool:
    """세션이 방치됐는지. started_at(시작 시각) 기준 — 마지막 활동 시각이 없어서다.

    ttl_seconds <= 0 이면 TTL 가드를 끈다(무한 유지).
    """
    if ttl_seconds <= 0:
        return False
    current = time.time() if now is None else now
    return (current - started_at) > ttl_seconds


def profile_signal_text(profile) -> str:
    """프로필의 도메인 신호를 임베딩용 한 덩어리로 모은다.

    description + domain_scopes + intent_hints 패턴 — 라우팅이 이미 쓰는 신호원과 같다.
    (이 텍스트는 프로필당 1회 임베딩해 캐시한다 — 매 턴 재계산 대상이 아니다.)
    """
    parts: list[str] = [getattr(profile, "name", "") or "", getattr(profile, "description", "") or ""]
    parts.extend(getattr(profile, "domain_scopes", None) or [])
    for hint in getattr(profile, "intent_hints", None) or []:
        parts.extend(getattr(hint, "patterns", None) or [])
    return " ".join(p for p in parts if p).strip()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def should_break_sticky(
    question_vec: list[float],
    sticky_vec: list[float],
    rival_vecs: dict[str, list[float]],
    cfg: StickyGuardConfig,
) -> tuple[bool, dict]:
    """다른 도메인이라는 강한 증거가 있으면 True(=sticky를 깬다).

    증거 없음(신호 약함·경합 없음)이면 False → sticky 유지. 이게 기본값이다.

    Returns:
        (깰지 여부, 판단 근거 dict — 로그용)
    """
    sticky_sim = _cosine(question_vec, sticky_vec)
    best_id, best_sim = "", 0.0
    for pid, vec in rival_vecs.items():
        sim = _cosine(question_vec, vec)
        if sim > best_sim:
            best_id, best_sim = pid, sim

    evidence = {
        "sticky_sim": round(sticky_sim, 4),
        "rival": best_id,
        "rival_sim": round(best_sim, 4),
        "margin": round(best_sim - sticky_sim, 4),
        "threshold": cfg.break_similarity,
        "required_margin": cfg.break_margin,
    }
    # 두 조건을 모두 넘겨야 "강한 증거"다. 하나만으론 깨지 않는다 —
    # 애매한 입력에서 워크플로우를 잃는 쪽이 더 큰 손해이기 때문.
    strong = best_sim >= cfg.break_similarity and (best_sim - sticky_sim) >= cfg.break_margin
    return strong, evidence
