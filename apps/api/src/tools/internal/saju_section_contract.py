"""사주 리포트 섹션 llmText 키 계약 — 정규화·검증의 단일 소스.

네 리포트(paper·compatibility·career·wealth)는 llmText 키 계약이 동일하다:
summary·advice·conclusion 필수, characteristics 선택. 규칙 텍스트 자체는 각
프롬프트 모듈(saju_prompts / saju_career_prompts / saju_wealth_prompts)에 복제돼
있으므로, 검증까지 툴마다 복제하면 계약이 7곳(코드4+프롬프트3)에 흩어져 한 곳만
고쳐도 검증이 거짓말을 하게 된다. 그래서 검증은 여기 한 곳에 둔다.
드리프트는 test_saju_section_contract.py가 프롬프트 규칙 텍스트와 대조해 잡는다.

**왜 이 모듈은 "공유 util 승격 금지"에 걸리지 않는가**: 그 원칙(도메인 분리)이 막는 것은
saju 도메인 코드가 *플랫폼 공용*으로 올라가 다른 도메인에 saju의 사정이 새는 것이다.
이 모듈은 saju 리포트 섹션 계약 전용 — 플랫폼이 아니라 saju 도메인 안에서만 공유되며,
다른 도메인이 쓸 일이 없다. (2026-07-16 사용자 확정)

제약 디코딩(generate_json / ollama format:"json")은 **문법**만 보장하고 키 이름은
보장하지 않는다. 실측: tenGodsShinsal이 프롬프트의 마크다운 불릿("- advice:")을 그대로
베껴 'advice:', 'conclusion:' 같은 키를 냈다. 소비 전에 여기서 걸러야 조용한 실패가 안 된다.
"""

from __future__ import annotations

from src.observability.logging import get_logger

logger = get_logger(__name__)

# 각 프롬프트 모듈의 규칙 6~9와 반드시 일치해야 한다. 규칙 텍스트가 유일한 계약 문서다.
SECTION_REQUIRED_KEYS: tuple[str, ...] = ("summary", "advice", "conclusion")
SECTION_OPTIONAL_KEYS: tuple[str, ...] = ("characteristics",)


def _is_filled(value: object) -> bool:
    """계약상 값은 비어있지 않은 문자열이다."""
    return isinstance(value, str) and bool(value.strip())


def normalize_section_keys(parsed: dict) -> dict:
    """LLM이 낸 키의 사소한 변형을 계약 키로 되돌린다.

    공백·트레일링 콜론·대소문자만 손본다. 의미를 추측한 매핑(예: 'tip' → 'advice')은
    하지 않는다 — 그건 모델이 다른 걸 말한 것이고, 조용히 끼워맞추면 검증의 의미가 없다.
    정규화가 키를 겹치게 만들면 내용이 있는 쪽을 남긴다.
    """
    out: dict = {}
    for raw_key, value in parsed.items():
        if not isinstance(raw_key, str):
            out[raw_key] = value
            continue
        key = raw_key.strip().strip(":").strip().lower()
        if key in out and _is_filled(out[key]) and not _is_filled(value):
            continue
        out[key] = value
    return out


def missing_section_keys(parsed: dict) -> list[str]:
    """계약상 필수인데 없거나 빈 키. 정규화 이후에 호출할 것."""
    return [k for k in SECTION_REQUIRED_KEYS if not _is_filled(parsed.get(k))]


def coerce_section(parsed: dict, *, report: str, section: str) -> tuple[dict, list[str]]:
    """섹션 응답을 계약에 맞춰 정규화하고 누락 키를 돌려준다.

    정규화로 실제 키를 고친 경우 로그를 남긴다 — 소리 없이 수리하면 "모델이 프롬프트를
    잘못 읽고 있다"는 신호가 묻혀, 정작 고쳐야 할 프롬프트가 그대로 남는다.

    Returns:
        (정규화된 dict, 누락된 필수 키 목록)
    """
    raw_keys = set(parsed)
    normalized = normalize_section_keys(parsed)
    repaired = sorted(raw_keys - set(normalized))
    if repaired:
        logger.warning(
            "saju_section_keys_repaired",
            report=report,
            section=section,
            raw_keys=repaired,
        )
    return normalized, missing_section_keys(normalized)
