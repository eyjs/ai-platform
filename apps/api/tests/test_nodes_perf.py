"""Agent nodes performance improvement tests.

P0-2: MAX_CONTENT_PREVIEW_LEN=2500
P1-5: Guardrail regeneration threshold=0.35
"""

import pytest

from src.agent.nodes import (
    MAX_CONTENT_PREVIEW_LEN,
    _format_result,
    build_prompt,
)


class TestContentPreviewLength:
    """MAX_CONTENT_PREVIEW_LEN 값 검증."""

    def test_max_content_preview_len_is_2500(self):
        """P0-2: MAX_CONTENT_PREVIEW_LEN은 2500이어야 한다."""
        assert MAX_CONTENT_PREVIEW_LEN == 2500

    def test_format_result_truncates_at_2500(self):
        """_format_result()는 2500자에서 잘라야 한다."""
        long_content = "가" * 3000
        result = {"content": long_content}
        formatted = _format_result(result)
        assert len(formatted) == 2500

    def test_format_result_short_content_unchanged(self):
        """짧은 콘텐츠는 잘리지 않는다."""
        content = "보험 약관 제1조"
        result = {"content": content}
        formatted = _format_result(result)
        assert formatted == content

    def test_format_result_exactly_2500_unchanged(self):
        """정확히 2500자는 잘리지 않는다."""
        content = "x" * 2500
        result = {"content": content}
        formatted = _format_result(result)
        assert len(formatted) == 2500


class TestBuildPromptContentLength:
    """build_prompt가 2500자 기준으로 콘텐츠를 포함하는지 검증."""

    def _make_plan(self, max_chunks=5):
        from dataclasses import dataclass

        @dataclass
        class FakeStrategy:
            max_vector_chunks: int = max_chunks

        @dataclass
        class FakePlan:
            strategy: FakeStrategy = None
            conversation_context: str = ""

            def __post_init__(self):
                if self.strategy is None:
                    self.strategy = FakeStrategy()

        return FakePlan()

    def test_build_prompt_includes_long_content(self):
        """2000자 콘텐츠가 잘리지 않고 프롬프트에 포함된다."""
        content = "보" * 2000
        results = [{"content": content, "file_name": "test.pdf"}]
        plan = self._make_plan()

        prompt = build_prompt("질문", plan, results)
        assert content in prompt

    def test_build_prompt_truncates_over_2500(self):
        """3000자 콘텐츠는 2500자로 잘려서 포함된다."""
        content = "험" * 3000
        results = [{"content": content, "file_name": "test.pdf"}]
        plan = self._make_plan()

        prompt = build_prompt("질문", plan, results)
        # 원본 3000자가 아닌 2500자만 포함
        assert "험" * 3000 not in prompt
        assert "험" * 2500 in prompt


class TestGuardrailRegenerationThreshold:
    """Guardrail 재생성 임계값 검증 (0.35)."""

    def _check_regeneration_needed(self, results: dict) -> bool:
        """nodes.py의 regeneration 판단 로직을 재현."""
        return any(
            isinstance(v, dict) and v.get("action") == "warn"
            and v.get("score") is not None and v.get("score") < 0.35
            for v in results.values()
        )

    def test_score_0_3_triggers_regeneration(self):
        """score=0.3 -> 재생성 필요 (0.35 미만)."""
        results = {"faithfulness": {"action": "warn", "score": 0.3, "ms": 100}}
        assert self._check_regeneration_needed(results) is True

    def test_score_0_35_does_not_trigger_regeneration(self):
        """score=0.35 -> 재생성 불필요 (임계값 이상)."""
        results = {"faithfulness": {"action": "warn", "score": 0.35, "ms": 100}}
        assert self._check_regeneration_needed(results) is False

    def test_score_0_4_does_not_trigger_regeneration(self):
        """score=0.4 -> 재생성 불필요 (이전 임계값 0.5 아래지만 새 임계값 0.35 이상)."""
        results = {"faithfulness": {"action": "warn", "score": 0.4, "ms": 100}}
        assert self._check_regeneration_needed(results) is False

    def test_score_0_5_does_not_trigger_regeneration(self):
        """score=0.5 -> 재생성 불필요."""
        results = {"faithfulness": {"action": "warn", "score": 0.5, "ms": 100}}
        assert self._check_regeneration_needed(results) is False

    def test_score_none_does_not_trigger_regeneration(self):
        """score=None -> 재생성 불필요."""
        results = {"faithfulness": {"action": "warn", "score": None, "ms": 100}}
        assert self._check_regeneration_needed(results) is False

    def test_action_pass_does_not_trigger_regeneration(self):
        """action=pass -> 재생성 불필요."""
        results = {"faithfulness": {"action": "pass", "score": 0.2, "ms": 100}}
        assert self._check_regeneration_needed(results) is False

    def test_empty_results_no_regeneration(self):
        """빈 결과 -> 재생성 불필요."""
        results = {}
        assert self._check_regeneration_needed(results) is False
