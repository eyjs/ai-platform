"""SajuReportCareerTool 단위 테스트.

Fake LLM(고정 JSON 반환)으로 5섹션 루프·부분실패 비차단·prior summary 주입을 검증.
상용 LLM 호출 없음 — conftest.py의 _block_external_llm_http 가 보장.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.agent_context import AgentContext
from src.tools.internal.saju_career_prompts import CAREER_V2_SECTION_KEYS
from src.tools.internal.saju_report_career import SajuReportCareerTool

# ──────────────────────────────────────────────
# 공통 픽스처
# ──────────────────────────────────────────────

SAMPLE_SAJU_DATA = {
    "basic": {"name": "테스트", "gender": "male", "birthYear": 1990},
    "premium": {
        "fourPillars": {
            "day": {"heavenlyStem": "갑", "earthlyBranch": "자"},
        },
        "interpretation": {
            "energyScore": {"wood": 40, "fire": 20, "earth": 15, "metal": 10, "water": 15},
            "tenGods": [
                {"key": "FOOD_GOD", "isRooted": True, "rootStrength": 2},
                {"key": "INDIRECT_RESOURCE", "isRooted": False, "rootStrength": 0},
            ],
            "yongsin": {"yongsin": "water", "heesin": "metal", "strategy": "수기 보강"},
            "daewoon": [
                {"startAge": 5, "endAge": 14, "gapja": "갑자", "tenGod": "FRIEND"},
                {"startAge": 15, "endAge": 24, "gapja": "을축", "tenGod": "INDIRECT_FRIEND"},
            ],
        },
    },
}


def _make_fake_llm(responses: list[str] | None = None) -> MagicMock:
    """고정 JSON을 순서대로 반환하는 Fake LLMProvider."""
    llm = MagicMock()
    if responses is None:
        # 기본: 모든 섹션 성공
        responses = [
            json.dumps({"summary": f"{key} 분석", "advice": f"{key} 조언", "conclusion": f"{key} 판정"})
            for key in CAREER_V2_SECTION_KEYS
        ]
    response_iter = iter(responses)
    llm.generate = AsyncMock(side_effect=lambda **kwargs: next(response_iter))
    return llm


def _make_context() -> AgentContext:
    return AgentContext(user_id="u1", session_id="s1")


# ──────────────────────────────────────────────
# 테스트: 섹션키 검증
# ──────────────────────────────────────────────


class TestCareerSectionKeys:
    def test_career_section_keys_count_is_5(self):
        """CAREER_V2_SECTION_KEYS는 정확히 5개여야 한다."""
        assert len(CAREER_V2_SECTION_KEYS) == 5

    def test_career_section_keys_names(self):
        """섹션키 순서 및 이름 검증."""
        expected = [
            "careerVessel",
            "careerTalent",
            "careerDirection",
            "careerTiming",
            "careerStrategy",
        ]
        assert CAREER_V2_SECTION_KEYS == expected


# ──────────────────────────────────────────────
# 테스트: 도구 기본 동작
# ──────────────────────────────────────────────


class TestSajuReportCareerToolBasic:
    async def test_missing_saju_data_returns_fail(self):
        """saju_data 없이 호출하면 실패를 반환해야 한다."""
        tool = SajuReportCareerTool(llm_provider=MagicMock())
        ctx = _make_context()

        result = await tool.execute(params={}, context=ctx)

        assert not result.success
        assert "saju_data" in result.error

    async def test_returns_report_type_career(self):
        """execute 결과의 report_type은 'career'여야 한다."""
        tool = SajuReportCareerTool(llm_provider=_make_fake_llm())
        ctx = _make_context()

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        assert result.success
        assert result.metadata["report_type"] == "career"

    async def test_returns_schema_version_report_v2(self):
        """schema_version은 'report.v2'여야 한다."""
        tool = SajuReportCareerTool(llm_provider=_make_fake_llm())
        ctx = _make_context()

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        assert result.metadata["schema_version"] == "report.v2"

    async def test_generates_5_sections(self):
        """5섹션이 모두 생성되어야 한다."""
        llm = _make_fake_llm()
        tool = SajuReportCareerTool(llm_provider=llm)
        ctx = _make_context()

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        assert result.success
        assert result.metadata["sections_completed"] == 5
        assert result.metadata["sections_total"] == 5
        # LLM은 5섹션 × 1호출
        assert llm.generate.call_count == 5

    async def test_all_section_keys_present_in_data(self):
        """data에 5개 섹션키가 모두 포함되어야 한다."""
        tool = SajuReportCareerTool(llm_provider=_make_fake_llm())
        ctx = _make_context()

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        for key in CAREER_V2_SECTION_KEYS:
            assert key in result.data, f"섹션키 {key}가 결과에 없음"

    async def test_schema_key_in_data(self):
        """data에 $schema 키가 포함되어야 한다."""
        tool = SajuReportCareerTool(llm_provider=_make_fake_llm())
        ctx = _make_context()

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        assert "$schema" in result.data
        assert result.data["$schema"] == "report.v2/career"


# ──────────────────────────────────────────────
# 테스트: 부분 실패 비차단
# ──────────────────────────────────────────────


class TestSajuReportCareerToolPartialFailure:
    async def test_llm_failure_does_not_block_other_sections(self):
        """LLM 전체 실패 시에도 failed_sections로 비차단 완료해야 한다."""
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
        tool = SajuReportCareerTool(llm_provider=llm)
        ctx = _make_context()

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        # 성공 처리(비차단) — sections_completed=0, failed_sections=5
        assert result.success
        assert result.metadata["sections_completed"] == 0
        assert len(result.metadata["failed_sections"]) == 5

    async def test_partial_failure_records_failed_section(self):
        """첫 섹션만 실패 시 나머지 4섹션은 성공해야 한다."""
        llm = MagicMock()
        good_response = json.dumps({"summary": "ok", "advice": "go", "conclusion": "yes"})
        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("첫 섹션 실패")
            return good_response

        llm.generate = AsyncMock(side_effect=_side_effect)
        tool = SajuReportCareerTool(llm_provider=llm)
        ctx = _make_context()

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        assert result.success
        assert result.metadata["sections_completed"] == 4
        assert result.metadata["failed_sections"] == ["careerVessel"]


# ──────────────────────────────────────────────
# 테스트: careerStrategy prior summary 주입
# ──────────────────────────────────────────────


class TestCareerStrategyPriorSummary:
    async def test_career_strategy_prompt_includes_prior_summaries(self):
        """careerStrategy 호출 시 앞 4섹션 summary가 user 프롬프트에 주입되어야 한다."""
        llm = MagicMock()
        captured_prompts: list[str] = []

        async def _capture_generate(*, prompt: str, system: str, **kwargs) -> str:
            captured_prompts.append(prompt)
            return json.dumps({"summary": "종합ok", "advice": "go", "conclusion": "yes"})

        llm.generate = AsyncMock(side_effect=_capture_generate)
        tool = SajuReportCareerTool(llm_provider=llm)
        ctx = _make_context()

        await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        # 마지막(5번째) 호출 = careerStrategy
        assert len(captured_prompts) == 5
        strategy_prompt = captured_prompts[-1]

        # 앞 섹션 summary가 포함되어야 한다
        assert "[이전 섹션 분석 결과 요약]" in strategy_prompt
        assert "[careerVessel]" in strategy_prompt
        assert "[careerTalent]" in strategy_prompt
        assert "[careerDirection]" in strategy_prompt
        assert "[careerTiming]" in strategy_prompt

    async def test_career_strategy_without_prior_sections_still_runs(self):
        """앞 섹션이 전부 실패해도 careerStrategy는 실행되어야 한다."""
        llm = MagicMock()
        call_count = 0

        async def _fail_first_four_then_succeed(**kwargs) -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 5:
                raise RuntimeError("앞 섹션 실패")
            return json.dumps({"summary": "전략ok", "advice": "go", "conclusion": "yes"})

        llm.generate = AsyncMock(side_effect=_fail_first_four_then_succeed)
        tool = SajuReportCareerTool(llm_provider=llm)
        ctx = _make_context()

        result = await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        assert result.success
        assert result.metadata["sections_completed"] == 1
        assert "careerStrategy" in result.data
        assert "careerStrategy" not in result.metadata["failed_sections"]


# ──────────────────────────────────────────────
# 테스트: 챗 맥락(_chatContext) 주입
# ──────────────────────────────────────────────


class TestCareerChatContextInjection:
    async def test_chat_context_is_injected_into_prompts(self):
        """_chatContext가 있으면 모든 섹션 프롬프트에 챗 맥락이 포함되어야 한다."""
        llm = MagicMock()
        captured_prompts: list[str] = []

        async def _capture(**kwargs) -> str:
            captured_prompts.append(kwargs.get("prompt", ""))
            return json.dumps({"summary": "ok", "advice": "go", "conclusion": "yes"})

        llm.generate = AsyncMock(side_effect=_capture)
        tool = SajuReportCareerTool(llm_provider=llm)
        ctx = _make_context()

        saju_with_chat = {
            **SAMPLE_SAJU_DATA,
            "_chatContext": "나는 프리랜서 디자이너인데 직장을 잡아야 할지 고민이야",
        }

        await tool.execute(params={"saju_data": saju_with_chat}, context=ctx)

        # 모든 섹션 프롬프트에 챗 맥락이 포함되어야 한다
        for prompt in captured_prompts:
            assert "프리랜서 디자이너" in prompt

    async def test_no_chat_context_does_not_add_section(self):
        """_chatContext가 없으면 챗 맥락 섹션이 프롬프트에 없어야 한다."""
        llm = MagicMock()
        captured_prompts: list[str] = []

        async def _capture(**kwargs) -> str:
            captured_prompts.append(kwargs.get("prompt", ""))
            return json.dumps({"summary": "ok", "advice": "go", "conclusion": "yes"})

        llm.generate = AsyncMock(side_effect=_capture)
        tool = SajuReportCareerTool(llm_provider=llm)
        ctx = _make_context()

        await tool.execute(params={"saju_data": SAMPLE_SAJU_DATA}, context=ctx)

        for prompt in captured_prompts:
            assert "방금 묘묘에게" not in prompt
