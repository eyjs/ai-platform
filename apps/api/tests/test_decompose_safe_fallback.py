"""decompose 안전 폴백 (진단 V2) — candidate[0] 맹목 위임 제거.

진단: decompose가 빈 계획/파싱 실패를 내면 `candidate_profiles[0]`에 질문 전체를
맹목 위임했다. **0번은 로딩 순서일 뿐**이라 8B가 JSON을 한 번 깨뜨리면 "보험 보상
절차"가 조용히 사주로 갔다 — 과거 "보험→사주" 실사고의 구조적 서식지이고,
LLM 일시 결함 1회 = 완전 오도메인 답변이었다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.supervisor.planner_llm import SupervisorPlanner


# 로딩 순서상 fortune-saju가 0번 — 예전 폴백이면 보험 질문이 여기로 갔다
_CANDIDATES = [
    {"id": "fortune-saju", "name": "사주", "description": "사주 운세"},
    {"id": "insurance-qa", "name": "보험", "description": "보험 약관"},
    {"id": "general-chat", "name": "일반", "description": "일반 대화"},
]

_QUESTION = "자동차보험 보상 절차 알려줘"


def _planner(llm_result=None, llm_error: Exception | None = None) -> SupervisorPlanner:
    llm = MagicMock()
    if llm_error is not None:
        llm.generate_json = AsyncMock(side_effect=llm_error)
    else:
        llm.generate_json = AsyncMock(return_value=llm_result)
    return SupervisorPlanner(llm)


@pytest.mark.asyncio
async def test_empty_plan_goes_to_general_not_candidate_zero():
    """★핵심 회귀: 빈 계획이 0번(사주)이 아니라 일반 프로필로 간다."""
    plan = await _planner({"delegations": []}).decompose(_QUESTION, None, _CANDIDATES)

    assert len(plan.delegations) == 1
    assert plan.delegations[0].profile == "general-chat"
    assert plan.delegations[0].profile != "fortune-saju", "0번 맹목 위임이 살아있다"


@pytest.mark.asyncio
async def test_llm_error_goes_to_general():
    """LLM 일시 결함 1회가 완전 오도메인 답변이 되면 안 된다."""
    plan = await _planner(llm_error=RuntimeError("8B down")).decompose(
        _QUESTION, None, _CANDIDATES,
    )
    assert plan.delegations[0].profile == "general-chat"


@pytest.mark.asyncio
async def test_malformed_json_goes_to_general():
    """파싱은 됐지만 형식이 깨진 경우(8B가 JSON을 살짝 깨뜨림)."""
    plan = await _planner({"delegations": [{"garbage": 1}]}).decompose(
        _QUESTION, None, _CANDIDATES,
    )
    assert plan.delegations[0].profile == "general-chat"


@pytest.mark.asyncio
async def test_question_is_preserved_in_fallback():
    plan = await _planner({"delegations": []}).decompose(_QUESTION, None, _CANDIDATES)
    assert plan.delegations[0].subquery == _QUESTION


@pytest.mark.asyncio
async def test_no_delegation_when_general_not_a_candidate():
    """일반 프로필이 인가 목록에 없으면 **아무 데도 안 보낸다**.

    아무 데나 보내느니 안 보내는 게 낫다 — 그게 V2의 병증이었다.
    """
    candidates = [c for c in _CANDIDATES if c["id"] != "general-chat"]
    plan = await _planner({"delegations": []}).decompose(_QUESTION, None, candidates)

    assert plan.delegations == []


@pytest.mark.asyncio
async def test_valid_plan_is_untouched():
    """정상 계획은 폴백이 건드리지 않는다."""
    plan = await _planner({
        "delegations": [
            {"profile": "insurance-qa", "subquery": "보상 절차", "reason": "보험 질문"},
        ],
    }).decompose(_QUESTION, None, _CANDIDATES)

    assert len(plan.delegations) == 1
    assert plan.delegations[0].profile == "insurance-qa"


@pytest.mark.asyncio
async def test_no_candidates_returns_empty():
    plan = await _planner({"delegations": []}).decompose(_QUESTION, None, [])
    assert plan.delegations == []


@pytest.mark.asyncio
async def test_fallback_profile_is_configurable(monkeypatch):
    """프로필 하드코딩 금지(절대규칙) — id는 설정에서 온다."""
    from src.supervisor import planner_llm

    monkeypatch.setattr(
        planner_llm.settings, "supervisor_fallback_profile_id", "insurance-qa",
    )
    plan = await _planner({"delegations": []}).decompose(_QUESTION, None, _CANDIDATES)
    assert plan.delegations[0].profile == "insurance-qa"
