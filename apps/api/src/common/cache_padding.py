"""Anthropic 프롬프트 캐싱 패딩 유틸 (도메인 중립).

Haiku 캐시 최소 4096 토큰(≈16384자) 미달 시 cacheable 블록을 세션 안정 콘텐츠로 채운다.
deterministic(workflow/engine.py)·agentic(agent/graphs.py) 경로가 공유한다.

레이어 중립(common): 상위 모듈 의존 없음. 도메인 지식을 포함하지 않는다 —
도메인별 패딩 콘텐츠가 필요하면 호출부가 `filler`로 주입한다(예: 워크플로우는
바인딩된 ContextAdapter의 `cache_padding_text`를 넘긴다).
"""

from __future__ import annotations

# Anthropic Haiku 프롬프트 캐싱 최소 토큰 수 (char/4 기준 → 최소 16384자)
CACHE_MIN_CHARS = 16384

# filler 미지정 시 쓰는 도메인 중립 패딩. 어떤 말투·형식·정체성도 지시하지 않으며,
# 캐시 prefix를 byte-stable하게 유지하기 위한 무해한 안정 텍스트일 뿐이다.
# UUID/timestamp/사용자 식별자 포함 금지(캐시 무효화 방지).
_NEUTRAL_FILLER = (
    "\n\n--- (캐시 안정용 여백 — 의미 없음, 참고하거나 인용하지 말 것) ---\n"
    "이 블록은 프롬프트 캐시 최소 크기를 맞추기 위한 중립 여백이다. "
    "말투·호칭·형식·캐릭터 정체성은 위 시스템 프롬프트를 그대로 따른다. "
    "이 블록의 내용은 응답에 반영하지 않는다.\n"
)


def build_cache_padding(needed_chars: int, filler: str = "") -> str:
    """cacheable 블록이 캐시 최소 크기 미달일 때 채울 패딩을 반환한다.

    Args:
        needed_chars: 추가로 필요한 글자 수.
        filler: 도메인별 패딩 콘텐츠(세션 안정 텍스트). 비면 도메인 중립 여백을 쓴다.
            UUID·timestamp·사용자 식별자를 포함해선 안 된다(캐시 안정성).
    """
    if needed_chars <= 0:
        return ""
    base = filler if filler else _NEUTRAL_FILLER
    repeats = (needed_chars // len(base)) + 1
    return (base * repeats)[:needed_chars]


def pad_to_min(text: str, min_chars: int = CACHE_MIN_CHARS, filler: str = "") -> str:
    """text가 캐시 최소 크기 미달이면 패딩을 덧붙여 반환한다(이미 충분하면 그대로).

    filler가 주어지면 그 콘텐츠로, 없으면 도메인 중립 여백으로 채운다.
    """
    if len(text) >= min_chars:
        return text
    return text + build_cache_padding(min_chars - len(text), filler=filler)
