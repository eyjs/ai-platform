"""응답 길이 2단 방어 테스트 — L3 간결 신호(1차) + max_output_tokens 하드 캡(2차)."""

from src.domain.agent_profile import AgentProfile
from src.domain.execution_plan import QuestionType
from src.domain.models import AgentMode
from src.router.strategy_builder import StrategyBuilder


def _build(query: str, *, max_output_tokens: int | None = None):
    builder = StrategyBuilder()
    profile = AgentProfile(
        id="p1", name="p1",
        system_prompt="너는 보험 상담 봇이다.",
        max_output_tokens=max_output_tokens,
    )
    return builder.build(
        profile=profile,
        question_type=QuestionType.STANDALONE,
        strategy=builder.get_strategy(QuestionType.STANDALONE),
        mode=AgentMode.DETERMINISTIC,
        tools=[],
        query=query,
    )


class TestBrevitySignal:
    def test_brevity_keyword_injects_directive(self):
        plan = _build("New간편간병보험 가입 나이 핵심만 알려줘")
        assert "[응답 지시]" in plan.volatile_system_prompt
        assert "간결" in plan.volatile_system_prompt

    def test_various_brevity_keywords(self):
        for q in ("간단히 설명해줘", "짧게 답해", "요약해줘", "한줄로 알려줘", "한 줄로"):
            plan = _build(q)
            assert "[응답 지시]" in plan.volatile_system_prompt, q

    def test_normal_question_no_directive(self):
        plan = _build("New간편간병보험 가입 나이 조건을 알려줘")
        assert "[응답 지시]" not in plan.volatile_system_prompt

    def test_date_grounding_preserved_with_brevity(self):
        """간결 지시가 기존 날짜 grounding을 대체하지 않고 뒤에 붙는다."""
        plan = _build("핵심만 알려줘")
        assert "[오늘 날짜]" in plan.volatile_system_prompt
        assert "[응답 지시]" in plan.volatile_system_prompt


class TestMaxOutputTokens:
    def test_profile_cap_flows_to_plan(self):
        plan = _build("질문", max_output_tokens=1500)
        assert plan.max_output_tokens == 1500

    def test_default_is_none(self):
        plan = _build("질문")
        assert plan.max_output_tokens is None


class TestProfileStoreRoundtrip:
    def test_yaml_field_roundtrip(self):
        from src.agent.profile_store import ProfileStore
        data = {"id": "t", "name": "t", "max_output_tokens": 2000}
        profile = ProfileStore._parse_profile(data)
        assert profile.max_output_tokens == 2000
        assert ProfileStore._profile_to_dict(profile)["max_output_tokens"] == 2000

    def test_yaml_field_absent_defaults_none(self):
        from src.agent.profile_store import ProfileStore
        profile = ProfileStore._parse_profile({"id": "t", "name": "t"})
        assert profile.max_output_tokens is None
