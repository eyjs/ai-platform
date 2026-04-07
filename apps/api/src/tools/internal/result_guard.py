"""검색 결과 가드. LLM에 전달하기 전 민감 콘텐츠 필터링."""

from src.locale.bundle import get_locale
from src.observability.logging import get_logger

logger = get_logger(__name__)


def guard_results(candidates: list[dict]) -> list[dict]:
    """가드 파사드. 내부 전략 체이닝."""
    return _pii_guard(candidates)


def _pii_guard(candidates: list[dict]) -> list[dict]:
    """개인정보 패턴이 포함된 청크를 마스킹."""
    guarded = []
    masked_count = 0
    for c in candidates:
        masked = _mask_pii(c["content"])
        if masked != c["content"]:
            masked_count += 1
        guarded.append({**c, "content": masked})

    if masked_count:
        logger.info("result_guard_pii_masked", count=masked_count)

    return guarded


def _mask_pii(text: str) -> str:
    """정규식 기반 PII 마스킹."""
    for pattern, replacement in get_locale().pii_result_guard:
        text = pattern.sub(replacement, text)
    return text
