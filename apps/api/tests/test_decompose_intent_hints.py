"""decompose 후보 렌더 — intent_hints.description 배선 (Phase 2).

배경: intent_hints는 (name, patterns, description)을 갖는데 description은 스키마·CRUD·
프론트까지 통과하면서 런타임 소비처가 0이었다. decompose LLM은 인텐트를 name(라벨)으로만
봐서 "INSURANCE_INQUIRY"라는 불투명한 문자열로 프로필을 골랐다. description(자연어)을
노출해 판단 근거를 준다. HybridTrigger의 keyword+description 2단 선례를 따른다.

patterns는 유지한다 — 프로필이 소유한 고신뢰 결정적 단축(_check_custom_intents).
"""

from src.supervisor.planner_llm import SupervisorPlanner


def _candidate(intents):
    return {
        "id": "insurance-qa", "name": "보험 상담봇", "description": "자동차보험 약관 안내",
        "domains": ["보험"], "intents": intents,
    }


# --- description 배선 ---


def test_intent_description_is_rendered():
    """★핵심: name뿐 아니라 description이 프롬프트에 실린다."""
    out = SupervisorPlanner._format_candidates([_candidate([
        {"name": "INSURANCE_INQUIRY", "description": "보험 상품·보장·보험료 질문"},
    ])])
    assert "INSURANCE_INQUIRY" in out
    assert "보험 상품·보장·보험료 질문" in out


def test_multiple_intents_rendered():
    out = SupervisorPlanner._format_candidates([_candidate([
        {"name": "A", "description": "가입 문의"},
        {"name": "B", "description": "해지 문의"},
    ])])
    assert "A(가입 문의)" in out
    assert "B(해지 문의)" in out


# --- 엣지: 안 깨진다 ---


def test_empty_description_falls_back_to_name():
    assert SupervisorPlanner._intent_strings(
        [{"name": "TASK", "description": ""}],
    ) == ["TASK"]


def test_missing_name_is_skipped():
    assert SupervisorPlanner._intent_strings(
        [{"name": "", "description": "x"}],
    ) == []


def test_none_intents():
    assert SupervisorPlanner._intent_strings(None) == []


def test_string_intents_backward_compat():
    """구버전/테스트가 문자열 리스트를 주면 그대로 둔다."""
    assert SupervisorPlanner._intent_strings(["FOO", "BAR"]) == ["FOO", "BAR"]


def test_profile_with_no_intent_hints_still_renders():
    out = SupervisorPlanner._format_candidates([_candidate([])])
    assert "insurance-qa" in out
    assert "담당 작업" not in out  # 인텐트 없으면 그 절 자체가 없다


def test_domains_and_intents_coexist():
    out = SupervisorPlanner._format_candidates([_candidate([
        {"name": "INQUIRY", "description": "문의"},
    ])])
    assert "담당 도메인: 보험" in out
    assert "담당 작업: INQUIRY(문의)" in out
