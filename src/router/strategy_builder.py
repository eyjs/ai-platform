"""Layer 3: Strategy Builder -- ExecutionPlan 조립.

STRATEGY_MATRIX 소유 + SearchScope + tools + system_prompt + guardrails + conversation_context 통합.
"""

import logging
from typing import List, Optional, Union

from src.domain.agent_profile import AgentProfile
from src.domain.models import (
    AgentMode, ResponsePolicy, SearchScope, SecurityLevel,
    SECURITY_HIERARCHY, resolve_domain_hierarchy,
)
from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall
from src.tools.base import ScopedTool, Tool

logger = logging.getLogger(__name__)

# QuestionType별 기본 전략 (L3 책임)
STRATEGY_MATRIX: dict[QuestionType, QuestionStrategy] = {
    QuestionType.GREETING: QuestionStrategy(needs_rag=False, history_turns=0),
    QuestionType.SYSTEM_META: QuestionStrategy(needs_rag=False, history_turns=0),
    QuestionType.STANDALONE: QuestionStrategy(needs_rag=True, history_turns=0),
    QuestionType.SAME_DOC_FOLLOWUP: QuestionStrategy(
        needs_rag=True, history_turns=3, max_vector_chunks=3,
    ),
    QuestionType.ANSWER_BASED_FOLLOWUP: QuestionStrategy(
        needs_rag=True, history_turns=5,
    ),
    QuestionType.CROSS_DOC_INTEGRATION: QuestionStrategy(
        needs_rag=True, history_turns=3, max_vector_chunks=8,
    ),
}


class StrategyBuilder:
    """ExecutionPlan 조립기."""

    def get_strategy(self, question_type: QuestionType) -> QuestionStrategy:
        """QuestionType에 대응하는 전략을 반환한다."""
        strategy = STRATEGY_MATRIX.get(question_type)
        if strategy is None:
            logger.warning("No strategy for QuestionType %s, defaulting to STANDALONE", question_type.value)
            strategy = STRATEGY_MATRIX[QuestionType.STANDALONE]
        return strategy

    def build(
        self,
        profile: AgentProfile,
        question_type: QuestionType,
        strategy: QuestionStrategy,
        mode: AgentMode,
        tools: List[Union[Tool, ScopedTool]],
        query: str = "",
        history: Optional[List[dict]] = None,
        user_security_level: str = "PUBLIC",
        prior_doc_ids: Optional[List[str]] = None,
        workflow_id: Optional[str] = None,
        workflow_step: Optional[str] = None,
    ) -> ExecutionPlan:
        # SearchScope 생성
        effective_security = min(
            self._security_rank(profile.security_level_max),
            self._security_rank(user_security_level),
        )
        security_level = self._rank_to_level(effective_security)

        # 도메인 계층 해석: "ga/contract" → ["ga/contract", "ga", "_common"]
        resolved_domains = resolve_domain_hierarchy(
            profile.domain_scopes,
            include_common=profile.include_common,
        )

        scope = SearchScope(
            domain_codes=resolved_domains,
            category_ids=profile.category_scopes if profile.category_scopes else None,
            security_level_max=security_level,
            allowed_doc_ids=prior_doc_ids if question_type == QuestionType.SAME_DOC_FOLLOWUP else None,
        )

        # conversation_context 조립 (L3 책임)
        conversation_context = ""
        if history and strategy.history_turns > 0:
            sanitized = self._sanitize_history(history[-strategy.history_turns:])
            conversation_context = "\n".join(
                f"{t['role']}: {t['content']}" for t in sanitized
            )

        # tool_groups 배정: 기본은 모든 도구를 한 그룹 (= 전부 병렬)
        tool_groups = self._build_tool_groups(query, tools, strategy)

        return ExecutionPlan(
            mode=mode,
            scope=scope,
            tool_groups=tool_groups,
            system_prompt=profile.system_prompt,
            guardrail_chain=profile.guardrails,
            question_type=question_type,
            strategy=strategy,
            workflow_id=workflow_id,
            workflow_step=workflow_step,
            conversation_context=conversation_context,
            response_policy=profile.response_policy,
            max_tool_calls=profile.max_tool_calls,
            agent_timeout_seconds=profile.agent_timeout_seconds,
        )

    @staticmethod
    def _sanitize_history(history: List[dict]) -> List[dict]:
        """대화 히스토리에서 PII 패턴을 마스킹한다."""
        import re
        pii_patterns = [
            (re.compile(r"\d{6}-[1-4]\d{6}"), "[주민번호]"),
            (re.compile(r"01[0-9]-\d{3,4}-\d{4}"), "[전화번호]"),
            (re.compile(r"\d{3,6}-\d{2,6}-\d{2,6}"), "[계좌번호]"),
        ]
        sanitized = []
        for turn in history:
            content = turn.get("content", "")
            for pat, replacement in pii_patterns:
                content = pat.sub(replacement, content)
            sanitized.append({**turn, "content": content})
        return sanitized

    @staticmethod
    def _build_tool_groups(
        query: str,
        tools: List[Union[Tool, ScopedTool]],
        strategy: QuestionStrategy,
    ) -> list[list[ToolCall]]:
        """도구 목록을 병렬 그룹으로 배정. 기본: 한 그룹 = 전부 병렬."""
        if not strategy.needs_rag or not tools:
            return []
        calls = [ToolCall(tool_name=t.name, params={"query": query}) for t in tools]
        return [calls]

    @staticmethod
    def _security_rank(level: str) -> int:
        rank = SECURITY_HIERARCHY.get(level)
        if rank is None:
            logger.warning("Unknown security level '%s', defaulting to INTERNAL (restrictive)", level)
            return SECURITY_HIERARCHY.get(SecurityLevel.INTERNAL, 1)
        return rank

    @staticmethod
    def _rank_to_level(rank: int) -> str:
        for level, r in SECURITY_HIERARCHY.items():
            if r == rank:
                return level
        logger.warning("Unknown security rank %d, defaulting to INTERNAL", rank)
        return SecurityLevel.INTERNAL
