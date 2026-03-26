"""검색 결과 노이즈 필터링. 내부 전략을 자유롭게 교체/추가 가능."""

from src.observability.logging import get_logger

logger = get_logger(__name__)

RELATIVE_GAP_RATIO = 0.3
MIN_KEEP_COUNT = 5


def filter_noise(candidates: list[dict]) -> list[dict]:
    """노이즈 필터 파사드. 내부 전략 체이닝."""
    result = _score_gap_filter(candidates)
    return result


def _score_gap_filter(candidates: list[dict]) -> list[dict]:
    """상대적 점수 갭 필터링. 1등 대비 30% 이상 하락 시 절단."""
    if len(candidates) <= MIN_KEEP_COUNT:
        return candidates

    top_score = candidates[0]["score"]
    if top_score <= 0:
        return candidates[:MIN_KEEP_COUNT]

    cutoff = len(candidates)
    for i in range(1, len(candidates)):
        gap_ratio = (top_score - candidates[i]["score"]) / top_score
        if gap_ratio >= RELATIVE_GAP_RATIO and i >= MIN_KEEP_COUNT:
            cutoff = i
            break

    if cutoff < len(candidates):
        logger.debug(
            "noise_filter_gap",
            before=len(candidates),
            after=cutoff,
            top_score=top_score,
        )

    return candidates[:cutoff]
