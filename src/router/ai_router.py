"""AI Router: 4-Layer 오케스트레이터.

Layer 0: Context Resolver (대명사 해소)
Layer 1: Intent Classifier (QuestionType 분류)
Layer 2: Mode Selector (agentic/workflow)
Layer 3: Strategy Builder (ExecutionPlan 조립)
"""

import logging
from typing import List, Optional, Union

from src.agent.profile import AgentProfile
from src.infrastructure.providers.base import LLMProvider
from src.router.context_resolver import ChainResolver
from src.router.execution_plan import ExecutionPlan
from src.router.intent_classifier import IntentClassifier
from src.router.mode_selector import ModeSelector
from src.router.strategy_builder import StrategyBuilder
from src.tools.base import ScopedTool, Tool

logger = logging.getLogger(__name__)


class AIRouter:
    """4-Layer AI Router."""

    def __init__(self, router_llm: LLMProvider):
        self._resolver = ChainResolver(router_llm)
        self._classifier = IntentClassifier(router_llm)
        self._mode_selector = ModeSelector()
        self._strategy_builder = StrategyBuilder()

    async def route(
        self,
        query: str,
        profile: AgentProfile,
        tools: List[Union[Tool, ScopedTool]],
        history: Optional[List[dict]] = None,
        user_security_level: str = "PUBLIC",
        prior_doc_ids: Optional[List[str]] = None,
    ) -> ExecutionPlan:
        """4-Layer 라우팅 실행."""
        history = history or []

        # Layer 0: Context Resolver
        resolution = await self._resolver.resolve(query, history)
        resolved_query = resolution.resolved_query

        # Layer 1: Intent Classifier
        question_type, strategy, custom_intent = await self._classifier.classify(
            resolved_query, history, profile,
        )

        # Layer 2: Mode Selector
        mode, workflow_id = self._mode_selector.select(
            resolved_query, profile, custom_intent,
        )

        # Layer 3: Strategy Builder
        conversation_context = ""
        if history and strategy.history_turns > 0:
            recent = history[-strategy.history_turns:]
            conversation_context = "\n".join(
                f"{t['role']}: {t['content']}" for t in recent
            )

        plan = self._strategy_builder.build(
            profile=profile,
            question_type=question_type,
            strategy=strategy,
            mode=mode,
            tools=tools,
            conversation_context=conversation_context,
            user_security_level=user_security_level,
            prior_doc_ids=prior_doc_ids,
            workflow_step=None,
        )

        logger.info(
            "Route: type=%s mode=%s tools=%d context=%s",
            question_type.value, mode, len(tools),
            resolution.method,
        )

        return plan
