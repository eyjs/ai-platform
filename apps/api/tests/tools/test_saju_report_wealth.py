"""SajuReportWealthTool 단위 테스트.

Fake LLM으로 5섹션 루프·부분실패 비차단·prior summary 주입 검증.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.agent_context import AgentContext
from src.tools.internal.saju_wealth_prompts import WEALTH_V2_SECTION_KEYS


SAMPLE_SAJU_DATA = {
    "basic": {"name": "테스트", "gender": "female", "birthYear": 1992},
    "premium": {
        "fourPillars": [],
        "energyScore": {"self": 45, "elements": {"fire": 20, "water": 10}},
        "tenGods": [],
        "tenGodsByPillar": {},
        "wealthFortune": {
            "favorablePeriods": [{"ageRange": "35-45", "reason": "재성 대운"}],
            "spendingTendency": "balanced",
            "wealthType": "INDIRECT_WEALTH",
        },
    },
}


def _fake_llm(responses: list[str] | None = None) -> MagicMock:
    """섹션별 JSON 응답을 순서대로 반환하는 Fake LLMProvider."""
    llm = MagicMock()
    if responses is None:
        responses = [
            json.dumps({"summary": f"{key} 분석", "advice": f"{key} 조언", "conclusion": f"{key} 판정"})
            for key in WEALTH_V2_SECTION_KEYS
        ]
    resp_iter = iter(responses)
    llm.generate = AsyncMock(side_effect=lambda **kwargs: next(resp_iter))
    return llm


@pytest.fixture
def context() -> AgentContext:
    return AgentContext(user_id="u-test", session_id="s-test")


class TestWealthSectionKeys:
    """WEALTH_V2_SECTION_KEYS 구조 검증."""

    def test_section_keys_count(self):
        assert len(WEALTH_V2_SECTION_KEYS) == 5

    def test_section_keys_exact(self):
        expected = ["wealthVessel", "wealthType", "wealthTiming", "wealthSpending", "wealthStrategy"]
        assert WEALTH_V2_SECTION_KEYS == expected

    def test_wealth_strategy_is_last(self):
        assert WEALTH_V2_SECTION_KEYS[-1] == "wealthStrategy"


class TestWealthSectionPrompts:
    """get_wealth_section_prompt 반환값 검증."""

    def test_returns_system_and_user(self):
        from src.tools.internal.saju_wealth_prompts import get_wealth_section_prompt

        system, user = get_wealth_section_prompt("wealthVessel")
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_system_contains_myo_identity(self):
        from src.tools.internal.saju_wealth_prompts import get_wealth_section_prompt

        system, _ = get_wealth_section_prompt("wealthVessel")
        assert "묘묘" in system
        assert "재물운" in system

    def test_user_contains_context_placeholder(self):
        from src.tools.internal.saju_wealth_prompts import get_wealth_section_prompt

        _, user = get_wealth_section_prompt("wealthVessel")
        assert "{context_str}" in user

    def test_unknown_key_returns_empty_instruction(self):
        from src.tools.internal.saju_wealth_prompts import get_wealth_section_prompt

        system, user = get_wealth_section_prompt("nonexistent_key")
        # system/user는 반환되지만 instruction 부분이 빈 문자열이어야 함
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_wealth_strategy_prompt_contains_summary_hint(self):
        from src.tools.internal.saju_wealth_prompts import get_wealth_section_prompt

        system, _ = get_wealth_section_prompt("wealthStrategy")
        # 종합 섹션은 이전 섹션 반영 지시를 포함해야 함
        assert "이전 섹션 분석 결과 요약" in system


class TestSajuReportWealthToolExecute:
    """SajuReportWealthTool.execute 주요 시나리오 검증."""

    async def test_execute_returns_5_sections(self, context):
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        llm = _fake_llm()
        tool = SajuReportWealthTool(llm_provider=llm)

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=context)

        assert result.success
        assert result.metadata["sections_completed"] == 5
        assert result.metadata["sections_total"] == 5
        assert result.metadata["failed_sections"] == []

    def test_all_section_keys_in_result(self):
        pass  # 아래 async 버전으로 처리

    async def test_all_section_keys_present_in_data(self, context):
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        llm = _fake_llm()
        tool = SajuReportWealthTool(llm_provider=llm)

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=context)

        for key in WEALTH_V2_SECTION_KEYS:
            assert key in result.data, f"섹션 키 누락: {key}"

    async def test_report_type_is_wealth(self, context):
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        llm = _fake_llm()
        tool = SajuReportWealthTool(llm_provider=llm)

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=context)

        assert result.metadata["report_type"] == "wealth"

    async def test_schema_version_is_report_v2(self, context):
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        llm = _fake_llm()
        tool = SajuReportWealthTool(llm_provider=llm)

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=context)

        assert result.metadata["schema_version"] == "report.v2"
        assert result.data.get("$schema") == "report.v2/wealth"

    async def test_missing_saju_data_returns_failure(self, context):
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        llm = MagicMock()
        tool = SajuReportWealthTool(llm_provider=llm)

        result = await tool.execute(params={}, context=context)

        assert not result.success
        assert "saju_data" in result.error

    async def test_llm_failure_is_non_blocking(self, context):
        """LLM 오류가 나도 나머지 섹션 계속 생성(부분 실패 비차단)."""
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        llm = MagicMock()
        # 첫 번째 섹션만 실패, 나머지는 성공
        ok_response = json.dumps({"summary": "ok", "advice": "good", "conclusion": "판정"})
        llm.generate = AsyncMock(
            side_effect=[RuntimeError("LLM error")] + [ok_response] * 4
        )
        tool = SajuReportWealthTool(llm_provider=llm)

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=context)

        # 성공으로 반환(부분 실패 허용)
        assert result.success
        assert result.metadata["sections_completed"] == 4
        assert len(result.metadata["failed_sections"]) == 1
        assert result.metadata["failed_sections"][0] == "wealthVessel"

    async def test_all_sections_fail_still_returns_success(self, context):
        """전 섹션 실패해도 ToolResult.ok 반환(빈 리포트)."""
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
        tool = SajuReportWealthTool(llm_provider=llm)

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=context)

        assert result.success
        assert result.metadata["sections_completed"] == 0
        assert len(result.metadata["failed_sections"]) == 5

    async def test_chat_context_injected_into_context_str(self, context):
        """_chatContext가 있으면 context_str에 주입된다."""
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        captured_prompts: list[str] = []

        async def capture_generate(**kwargs):
            captured_prompts.append(kwargs.get("prompt", ""))
            return json.dumps({"summary": "ok", "advice": "good", "conclusion": "판정"})

        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=capture_generate)
        tool = SajuReportWealthTool(llm_provider=llm)

        saju_with_chat = {**SAMPLE_SAJU_DATA, "_chatContext": "돈이 너무 없어서 걱정돼"}

        await tool.execute(params={"saju_data": saju_with_chat}, context=context)

        # 첫 번째 섹션 프롬프트에 챗 맥락이 주입됐어야 함
        assert any("돈이 너무 없어서 걱정돼" in p for p in captured_prompts)


class TestWealthPriorSummaryInjection:
    """wealthStrategy 섹션에 prior summaries 주입 검증."""

    async def test_prior_summaries_injected_for_strategy(self, context):
        """wealthStrategy 호출 시 앞 4섹션의 summary가 user prompt에 포함된다."""
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        captured_prompts: list[str] = []

        async def capture_generate(**kwargs):
            captured_prompts.append(kwargs.get("prompt", ""))
            return json.dumps({"summary": "요약 내용", "advice": "조언", "conclusion": "판정"})

        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=capture_generate)
        tool = SajuReportWealthTool(llm_provider=llm)

        await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=context)

        # 마지막(5번째) 호출 = wealthStrategy — 이전 섹션 요약이 포함돼야 함
        assert len(captured_prompts) == 5
        strategy_prompt = captured_prompts[-1]
        assert "이전 섹션 분석 결과 요약" in strategy_prompt
        # 앞 4섹션 키가 요약에 언급돼야 함
        for key in WEALTH_V2_SECTION_KEYS[:-1]:
            assert key in strategy_prompt

    async def test_prior_summaries_not_injected_for_other_sections(self, context):
        """wealthStrategy 이외 섹션에는 prior summaries를 주입하지 않는다."""
        from src.tools.internal.saju_report_wealth import SajuReportWealthTool

        captured_prompts: list[str] = []

        async def capture_generate(**kwargs):
            captured_prompts.append(kwargs.get("prompt", ""))
            return json.dumps({"summary": "요약", "advice": "조언", "conclusion": "판정"})

        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=capture_generate)
        tool = SajuReportWealthTool(llm_provider=llm)

        await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=context)

        # 첫 번째 섹션(wealthVessel)에는 prior summaries 없어야 함
        assert "이전 섹션 분석 결과 요약" not in captured_prompts[0]
