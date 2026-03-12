"""AI Router: 4-Layer 오케스트레이터.

Layer 0: Context Resolver (대명사 해소)
Layer 1: Intent Classifier (QuestionType 분류)
Layer 2: Mode Selector (agentic/workflow)
Layer 3: Strategy Builder (전략 + SearchScope + conversation_context + ExecutionPlan 조립)

각 Layer 실패 시 안전한 기본값으로 폴백하여 전체 라우팅이 죽지 않도록 보장.
"""

import time
from typing import List, Optional, Union

from src.agent.profile import AgentProfile
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.router.context_resolver import ChainResolver, ResolutionResult
from src.router.execution_plan import ExecutionPlan, QuestionType
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
        t_start = time.time()
        history = history or []

        # Layer 0: Context Resolver
        resolved_query, resolution = await self._run_l0(query, history)

        # Layer 1: Intent Classifier
        question_type, custom_intent = await self._run_l1(
            resolved_query, history, profile,
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

        # Layer 3: Strategy Builder
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

        total_ms = (time.time() - t_start) * 1000
        logger.info(
            "route_complete",
            question_type=question_type.value,
            mode=mode.value,
            tools_count=len(tools),
            context_method=resolution.method,
            total_ms=round(total_ms, 1),
        )

        return plan

    async def _run_l0(
        self, query: str, history: List[dict],
    ) -> tuple[str, ResolutionResult]:
        """Layer 0 실행. 실패 시 원본 쿼리를 passthrough로 반환."""
        t0 = time.time()
        try:
            resolution = await self._resolver.resolve(query, history)
            resolved_query = resolution.resolved_query
        except Exception as e:
            logger.warning("L0_fallback", error=str(e))
            resolution = ResolutionResult(
                resolved_query=query,
                original_query=query,
                confidence=0.0,
                method="fallback",
            )
            resolved_query = query
        l0_ms = (time.time() - t0) * 1000
        logger.info(
            "L0_context_resolve",
            method=resolution.method,
            confidence=resolution.confidence,
            changed=resolution.original_query != resolved_query,
            latency_ms=round(l0_ms, 1),
        )
        return resolved_query, resolution

    async def _run_l1(
        self,
        query: str,
        history: List[dict],
        profile: AgentProfile,
    ) -> tuple[QuestionType, Optional[str]]:
        """Layer 1 실행. 실패 시 STANDALONE으로 폴백."""
        t1 = time.time()
        try:
            question_type, custom_intent = await self._classifier.classify(
                query, history, profile,
            )
        except Exception as e:
            logger.warning("L1_fallback", error=str(e))
            question_type = QuestionType.STANDALONE
            custom_intent = None
        l1_ms = (time.time() - t1) * 1000
        logger.info(
            "L1_intent_classify",
            question_type=question_type.value,
            custom_intent=custom_intent,
            latency_ms=round(l1_ms, 1),
        )
        return question_type, custom_intent
