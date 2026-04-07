"""AI Router: 4-Layer 오케스트레이터.

Layer 0: Context Resolver (대명사 해소)
Layer 1: Intent Classifier (QuestionType 분류)
Layer 2: Mode Selector (agentic/workflow)
Layer 3: Strategy Builder (전략 + SearchScope + conversation_context + ExecutionPlan 조립)

예외 정책:
- AIError(LLM 파싱 실패, 의도 분류 오류) → 잡아서 안전한 기본값으로 Fallback
- InfraError/시스템 에러(DB, 네트워크) → 잡지 않고 상위로 전파
"""

import time
from typing import List, Optional, Union

from src.domain.agent_profile import AgentProfile
from src.common.exceptions import AIError, RouterAIError
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.router.context_resolver import ChainResolver, ResolutionResult
from src.router.execution_plan import ExecutionPlan, QuestionType
from src.router.intent_classifier import IntentClassifier
from src.router.mode_selector import ModeSelector
from src.router.strategy_builder import StrategyBuilder
from src.tools.base import ScopedTool, Tool

logger = get_logger(__name__)

# AI 결함으로 간주하여 Fallback 처리할 예외 목록
# LLM 파싱 실패, JSON 형식 오류, 의도 분류 이상 등
#
# 주의: ValueError/KeyError/TypeError는 범위가 넓다.
# 이 튜플은 _run_l0/_run_l1처럼 LLM 호출 직후의 좁은 try 블록에서만 사용할 것.
# 넓은 범위에서 사용하면 프로그래밍 버그를 삼킬 위험이 있다.
_AI_RECOVERABLE = (
    AIError,          # 우리 예외 계층
    ValueError,       # JSON 파싱, enum 변환 실패
    KeyError,         # LLM 응답에서 필수 필드 누락
    TypeError,        # LLM 응답 타입 불일치
)


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
        skip_context_resolve: bool = False,
        external_context: str = "",
    ) -> ExecutionPlan:
        """4-Layer 라우팅 실행."""
        t_start = time.time()
        history = history or []

        # Layer 0: Context Resolver
        # chatbot_id가 명시적으로 전달된 경우 L0을 건너뛰고 passthrough 처리
        if skip_context_resolve:
            resolved_query = query
            resolution = ResolutionResult(
                resolved_query=query,
                original_query=query,
                confidence=1.0,
                method="passthrough",
            )
            logger.info(
                "L0_context_resolve",
                layer="ROUTER", component="ContextResolver",
                method="passthrough",
                confidence=1.0,
                changed=False,
                latency_ms=0.0,
                reason="skip_context_resolve (chatbot_id explicit)",
            )
        else:
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
            layer="ROUTER", component="ModeSelector",
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
            query=resolved_query,
            history=history,
            user_security_level=user_security_level,
            prior_doc_ids=prior_doc_ids,
            workflow_id=workflow_id,
            workflow_step=None,
            external_context=external_context,
        )
        l3_ms = (time.time() - t3) * 1000
        logger.info(
            "L3_strategy_build",
            layer="ROUTER", component="StrategyBuilder",
            tools_count=sum(len(g) for g in plan.tool_groups),
            guardrails=plan.guardrail_chain,
            scope_domains=plan.scope.domain_codes,
            security_max=plan.scope.security_level_max,
            needs_rag=strategy.needs_rag,
            history_turns=strategy.history_turns,
            has_external_context=bool(external_context),
            has_conversation_context=bool(plan.conversation_context),
            latency_ms=round(l3_ms, 1),
        )

        total_ms = (time.time() - t_start) * 1000
        logger.info(
            "route_complete",
            layer="ROUTER",
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
        """Layer 0 실행.

        AI 결함(LLM 파싱 실패 등) → 원본 쿼리로 Fallback.
        시스템 결함(DB, 네트워크) → 상위로 전파.
        """
        t0 = time.time()
        try:
            resolution = await self._resolver.resolve(query, history)
            resolved_query = resolution.resolved_query
        except _AI_RECOVERABLE as e:
            logger.warning(
                "L0_fallback",
                layer="ROUTER", component="ContextResolver",
                error_code="ERR_ROUTER_L0_FALLBACK",
                error=str(e),
            )
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
            layer="ROUTER", component="ContextResolver",
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
        """Layer 1 실행.

        AI 결함 → STANDALONE으로 Fallback.
        시스템 결함 → 상위로 전파.
        """
        t1 = time.time()
        try:
            question_type, custom_intent = await self._classifier.classify(
                query, history, profile,
            )
        except _AI_RECOVERABLE as e:
            logger.warning(
                "L1_fallback",
                layer="ROUTER", component="IntentClassifier",
                error_code="ERR_ROUTER_L1_FALLBACK",
                error=str(e),
            )
            question_type = QuestionType.STANDALONE
            custom_intent = None
        l1_ms = (time.time() - t1) * 1000
        logger.info(
            "L1_intent_classify",
            layer="ROUTER", component="IntentClassifier",
            question_type=question_type.value,
            custom_intent=custom_intent,
            latency_ms=round(l1_ms, 1),
        )
        return question_type, custom_intent
