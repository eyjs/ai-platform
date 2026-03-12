"""Layer 3: Strategy Builder — ExecutionPlan 조립.

SearchScope + tools + system_prompt + guardrails 통합.
"""

import logging
from typing import List, Optional, Union

from src.agent.profile import AgentProfile
from src.router.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, SearchScope
from src.tools.base import ScopedTool, Tool

logger = logging.getLogger(__name__)


class StrategyBuilder:
    """ExecutionPlan 조립기."""

    def build(
        self,
        profile: AgentProfile,
        question_type: QuestionType,
        strategy: QuestionStrategy,
        mode: str,
        tools: List[Union[Tool, ScopedTool]],
        conversation_context: str = "",
        user_security_level: str = "PUBLIC",
        prior_doc_ids: Optional[List[str]] = None,
        workflow_step: Optional[str] = None,
    ) -> ExecutionPlan:
        # SearchScope 생성
        effective_security = min(
            self._security_rank(profile.security_level_max),
            self._security_rank(user_security_level),
        )
        security_level = self._rank_to_level(effective_security)

        scope = SearchScope(
            domain_codes=profile.domain_scopes,
            category_ids=profile.category_scopes if profile.category_scopes else None,
            security_level_max=security_level,
            allowed_doc_ids=prior_doc_ids if question_type == QuestionType.SAME_DOC_FOLLOWUP else None,
        )

        return ExecutionPlan(
            mode=mode,
            scope=scope,
            tools=tools,
            system_prompt=profile.system_prompt,
            guardrail_chain=profile.guardrails,
            question_type=question_type,
            strategy=strategy,
            workflow_step=workflow_step,
            conversation_context=conversation_context,
        )

    _SECURITY_RANKS = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "SECRET": 3}

    def _security_rank(self, level: str) -> int:
        return self._SECURITY_RANKS.get(level, 0)

    def _rank_to_level(self, rank: int) -> str:
        for level, r in self._SECURITY_RANKS.items():
            if r == rank:
                return level
        return "PUBLIC"
