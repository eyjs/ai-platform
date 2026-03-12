"""AI Router: 4-Layer 오케스트레이터.

Layer 0: Context Resolver (대명사 해소)
Layer 1: Intent Classifier (QuestionType 분류)
Layer 2: Mode Selector (agentic/workflow)
Layer 3: Strategy Builder (전략 + SearchScope + conversation_context + ExecutionPlan 조립)
"""

import time
from typing import List, Optional, Union

from src.agent.profile import AgentProfile
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.router.context_resolver import ChainResolver
from src.router.execution_plan import ExecutionPlan
from src.router.intent_classifier import IntentClassifier
from src.router.mode_selector import ModeSelector
from src.router.strategy_builder import StrategyBuilder
from src.tools.base import ScopedTool, Tool

logger = get_logger(__name__)


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
        t0 = time.time()
        resolution = await self._resolver.resolve(query, history)
        resolved_query = resolution.resolved_query
        l0_ms = (time.time() - t0) * 1000
        logger.info(
            "L0_context_resolve",
            method=resolution.method,
            confidence=resolution.confidence,
            changed=resolution.original_query != resolved_query,
            latency_ms=round(l0_ms, 1),
        )

        # Layer 1: Intent Classifier (QuestionType만 반환)
        t1 = time.time()
        question_type, custom_intent = await self._classifier.classify(
            resolved_query, history, profile,
        )
        l1_ms = (time.time() - t1) * 1000
        logger.info(
            "L1_intent_classify",
            question_type=question_type.value,
            custom_intent=custom_intent,
            latency_ms=round(l1_ms, 1),
        )

        # Layer 2: Mode Selector
        t2 = time.time()
        mode, workflow_id = self._mode_selector.select(
            resolved_query, profile, custom_intent,
        )
        l2_ms = (time.time() - t2) * 1000
        logger.info(
            "L2_mode_select",
            mode=mode.value,
            workflow_id=workflow_id,
            latency_ms=round(l2_ms, 1),
        )

        # Layer 3: Strategy Builder (전략 매핑 + ExecutionPlan 조립 + conversation_context)
        t3 = time.time()
        strategy = self._strategy_builder.get_strategy(question_type)
        plan = self._strategy_builder.build(
            profile=profile,
            question_type=question_type,
            strategy=strategy,
            mode=mode,
            tools=tools,
            history=history,
            user_security_level=user_security_level,
            prior_doc_ids=prior_doc_ids,
            workflow_step=None,
        )
        l3_ms = (time.time() - t3) * 1000
        logger.info(
            "L3_strategy_build",
            tools_count=len(plan.tools),
            guardrails=plan.guardrail_chain,
            scope_domains=plan.scope.domain_codes,
            security_max=plan.scope.security_level_max,
            needs_rag=strategy.needs_rag,
            history_turns=strategy.history_turns,
            latency_ms=round(l3_ms, 1),
        )

        total_ms = l0_ms + l1_ms + l2_ms + l3_ms
        logger.info(
            "route_complete",
            question_type=question_type.value,
            mode=mode.value,
            tools_count=len(tools),
            context_method=resolution.method,
            total_ms=round(total_ms, 1),
        )

        return plan
