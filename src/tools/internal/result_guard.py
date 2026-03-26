"""검색 결과 가드. LLM에 전달하기 전 민감 콘텐츠 필터링."""

import re

from src.observability.logging import get_logger

logger = get_logger(__name__)

_RE_RESIDENT = re.compile(r"\d{6}-[1-4]\d{6}")
_RE_PHONE = re.compile(r"01[016789]-\d{3,4}-\d{4}")
_RE_ACCOUNT = re.compile(r"\d{3,6}-\d{2,6}-\d{2,6}")


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
    text = _RE_RESIDENT.sub("[주민번호]", text)
    text = _RE_PHONE.sub("[전화번호]", text)
    text = _RE_ACCOUNT.sub("[계좌번호]", text)
    return text
