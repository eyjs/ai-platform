"""사주 리포트 섹션 llmText 키 계약 — 정규화·검증 단위 테스트.

배경: 제약 디코딩(generate_json / ollama format:"json")은 문법상 유효한 JSON만
보장하고 키 이름은 보장하지 않는다. 실측(2026-07-16)에서 모델이 프롬프트의 마크다운
불릿("- advice:")을 그대로 베껴 'advice:' 같은 키를 냈고, 검증이 없어 조용히 통과했다.
"""

import re

import pytest

from src.tools.internal.saju_section_contract import (
    SECTION_OPTIONAL_KEYS,
    SECTION_REQUIRED_KEYS,
    coerce_section,
    missing_section_keys,
    normalize_section_keys,
)


# --- 계약 상수 ↔ 프롬프트 규칙 드리프트 ---
#
# 규칙 텍스트는 프롬프트 모듈 3곳에 복제돼 있고 검증은 saju_section_contract 한 곳에 있다.
# 둘이 어긋나면 검증이 거짓말을 한다 — 아래 테스트가 그 드리프트를 잡는 유일한 방어선이다.

_PROMPT_MODULES = [
    "src.tools.internal.saju_prompts",
    "src.tools.internal.saju_career_prompts",
    "src.tools.internal.saju_wealth_prompts",
]


def _rules_text(module_path: str) -> str:
    import importlib

    mod = importlib.import_module(module_path)
    # 각 모듈의 공통 규칙 상수(이름이 다를 수 있어 전체 소스에서 찾는다)
    return "\n".join(
        v for v in vars(mod).values()
        if isinstance(v, str) and "(필수)" in v
    )


@pytest.mark.parametrize("module_path", _PROMPT_MODULES)
def test_prompt_rules_declare_the_same_required_keys(module_path):
    """프롬프트가 '필수'라고 선언한 키 = SECTION_REQUIRED_KEYS 여야 한다."""
    text = _rules_text(module_path)
    assert text, f"{module_path}에서 규칙 텍스트를 못 찾음 — 테스트가 무력화됐다"
    declared = set(re.findall(r"(\w+) \(필수\)", text))
    assert declared == set(SECTION_REQUIRED_KEYS), (
        f"{module_path}의 필수 키 선언({sorted(declared)})이 "
        f"계약({sorted(SECTION_REQUIRED_KEYS)})과 다르다"
    )


@pytest.mark.parametrize("module_path", _PROMPT_MODULES)
def test_prompt_rules_declare_the_same_optional_keys(module_path):
    text = _rules_text(module_path)
    declared = set(re.findall(r"(\w+) \(선택\)", text))
    assert declared == set(SECTION_OPTIONAL_KEYS), (
        f"{module_path}의 선택 키 선언({sorted(declared)})이 "
        f"계약({sorted(SECTION_OPTIONAL_KEYS)})과 다르다"
    )


# --- 정규화 ---


def test_strips_trailing_colon():
    """실측 사례: 'advice:' → 'advice'."""
    out = normalize_section_keys({"advice:": "조언", "conclusion:": "결론"})
    assert out == {"advice": "조언", "conclusion": "결론"}


def test_strips_whitespace_and_lowercases():
    out = normalize_section_keys({" Summary ": "요약", "ADVICE": "조언"})
    assert out == {"summary": "요약", "advice": "조언"}


def test_does_not_guess_semantic_aliases():
    """'tip'을 advice로 끼워맞추지 않는다 — 모델이 다른 걸 말한 것이고,
    조용히 매핑하면 검증이 무의미해진다."""
    out = normalize_section_keys({"tip": "조언 비슷한 것"})
    assert "advice" not in out
    assert out == {"tip": "조언 비슷한 것"}


def test_collision_keeps_filled_value():
    """정규화가 키를 겹치게 만들면 내용이 있는 쪽을 남긴다."""
    out = normalize_section_keys({"advice": "진짜 조언", "advice:": "  "})
    assert out["advice"] == "진짜 조언"


def test_non_string_key_passes_through():
    out = normalize_section_keys({1: "숫자키"})
    assert out == {1: "숫자키"}


# --- 검증 ---


def test_missing_reports_absent_required_keys():
    assert missing_section_keys({"summary": "요약"}) == ["advice", "conclusion"]


def test_blank_string_is_missing():
    assert missing_section_keys(
        {"summary": "요약", "advice": "", "conclusion": "   "},
    ) == ["advice", "conclusion"]


def test_non_string_value_is_missing():
    """계약상 값은 문자열이다 — dict/list가 오면 소비처가 렌더링하지 못한다."""
    assert missing_section_keys(
        {"summary": {"nested": "x"}, "advice": ["a"], "conclusion": "결론"},
    ) == ["summary", "advice"]


def test_complete_section_has_no_missing():
    assert missing_section_keys(
        {"summary": "요약", "advice": "조언", "conclusion": "결론"},
    ) == []


def test_optional_key_absence_is_not_missing():
    """characteristics는 선택 — 없어도 계약 위반이 아니다."""
    assert missing_section_keys(
        {"summary": "요약", "advice": "조언", "conclusion": "결론"},
    ) == []
