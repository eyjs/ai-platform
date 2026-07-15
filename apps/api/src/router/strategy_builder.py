"""Layer 3: Strategy Builder -- ExecutionPlan 조립.

STRATEGY_MATRIX 소유 + SearchScope + tools + system_prompt + guardrails + conversation_context 통합.
"""

import logging
import re
from datetime import datetime
from typing import List, Optional, Union

from src.config import settings
from src.domain.agent_profile import AgentProfile
from src.domain.models import (
    AgentMode, ResponsePolicy, SearchScope, SecurityLevel,
    SECURITY_HIERARCHY, resolve_domain_hierarchy,
)
from src.domain.execution_plan import ExecutionPlan, QuestionStrategy, QuestionType, ToolCall
from src.tools.base import ScopedTool, Tool

logger = logging.getLogger(__name__)

# QuestionType별 기본 전략 (L3 책임)
# max_vector_chunks 8: 경계선 마진 — 후보 풀이 실행마다 조금씩 달라(확장/이웃/그래프)
# min-max 정규화 점수가 흔들리는데, 정원 5는 정답 청크(예: fused 0.64, 6위권)를
# 실행에 따라 자르거나 살리는 비결정 오답을 만든다(실사고: 실손 가입자격).
# 8이면 문서 다양성 캡(DIVERSITY_MIN_TOP_K=8)도 함께 활성화된다.
STRATEGY_MATRIX: dict[QuestionType, QuestionStrategy] = {
    QuestionType.GREETING: QuestionStrategy(needs_rag=False, history_turns=0),
    QuestionType.SYSTEM_META: QuestionStrategy(needs_rag=False, history_turns=0),
    QuestionType.STANDALONE: QuestionStrategy(
        needs_rag=True, history_turns=3, max_vector_chunks=8,
    ),
    QuestionType.SAME_DOC_FOLLOWUP: QuestionStrategy(
        # 문서 고정(allowed_doc_ids) 후속질문 — 좁은 정원이 의도값.
        # 부족하면 무답변 확장 재시도가 3→6으로 커버한다.
        needs_rag=True, history_turns=3, max_vector_chunks=3,
    ),
    QuestionType.ANSWER_BASED_FOLLOWUP: QuestionStrategy(
        needs_rag=True, history_turns=5, max_vector_chunks=8,
    ),
    QuestionType.CROSS_DOC_INTEGRATION: QuestionStrategy(
        needs_rag=True, history_turns=3, max_vector_chunks=10,
    ),
}


class StrategyBuilder:
    """ExecutionPlan 조립기."""

    # 간결 요청 신호 (결정론적 — 로컬 LLM 원칙: 판단은 신호로).
    _BREVITY_PATTERN = re.compile(r"핵심만|간단히|간략히|짧게|요약해|요약만|한\s?줄로")

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
        external_context: str = "",
        tenant_id: Optional[str] = None,
        session_scope_id: Optional[str] = None,
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
            # NOT WIRED: vector_store에 category 컬럼/파라미터 없음 — category_ids 세팅되나 질의에 미사용
            category_ids=profile.category_scopes if profile.category_scopes else None,
            security_level_max=security_level,
            allowed_doc_ids=prior_doc_ids if question_type == QuestionType.SAME_DOC_FOLLOWUP else None,
            tenant_id=tenant_id,
            session_id=session_scope_id,  # 세션 업로드 문서 검색 시에만 격리 필터 적용(Step26)
        )

        # external_context가 있으면 history_turns를 최소 3으로 보장
        # 사주 채팅 등 외부 컨텍스트 기반 대화는 히스토리 연속성이 필수
        effective_history_turns = strategy.history_turns
        if external_context and effective_history_turns < 3:
            effective_history_turns = 3

        # conversation_context 조립 (L3 책임)
        conversation_context = ""
        if history and effective_history_turns > 0:
            sanitized = self._sanitize_history(history[-effective_history_turns:])
            conversation_context = "\n".join(
                f"{t['role']}: {t['content']}" for t in sanitized
            )

        # tool_groups 배정
        # agentic 모드는 LLM이 자율적으로 도구를 선택하므로 needs_rag와 무관하게 항상 도구 제공
        if mode == AgentMode.AGENTIC and tools:
            tool_groups = [[ToolCall(tool_name=t.name, params={}) for t in tools]]
        else:
            tool_groups = self._build_tool_groups(query, tools, strategy)

        # cacheable_system_prompt: persona + external_context (세션 내 안정 바이트 → 캐시 경계).
        # volatile_system_prompt: 날짜/per-turn 지시 (매 요청 변동 → 캐시 경계 밖).
        cacheable_system_prompt = profile.system_prompt
        if external_context:
            cacheable_system_prompt = (
                f"{profile.system_prompt}\n\n"
                f"--- 참고 컨텍스트 ---\n{external_context}"
            ) if profile.system_prompt else f"--- 참고 컨텍스트 ---\n{external_context}"

        # 날짜 주입 — volatile_system_prompt 로 분리해 cacheable 경계를 보호한다.
        # LLM이 "올해"/시기를 정확히 인식하도록 (연도 grounding).
        # 날짜가 cacheable 에 포함되면 날짜가 바뀔 때마다 캐시 무효화 발생.
        volatile_system_prompt = ""
        if cacheable_system_prompt:
            today = datetime.now()
            # 날짜는 volatile(캐시 밖). 톤은 부드럽게(페르소나 안 깨게), 사실만 명확히.
            # (실 saju 경로는 백엔드 directive에도 날짜가 들어가 LLM이 존중 — 여기선 명령형 불필요.)
            volatile_system_prompt = (
                f"[오늘 날짜] {today.year}년 {today.month}월 {today.day}일. "
                f"'올해'는 {today.year}년, '내년'은 {today.year + 1}년, '작년'은 {today.year - 1}년이다."
            )

        # 간결 신호 감지 — 사용자가 짧은 답을 요청하면 생성 지시를 주입한다(1차 방어).
        # 결정론적 패턴 매칭(LLM 판단 없음). per-turn 지시라 volatile에 붙여 캐시 경계 보호.
        if query and self._BREVITY_PATTERN.search(query):
            brevity = (
                "[응답 지시] 사용자가 간결한 답변을 요청했다. "
                "핵심 정보만 골라 짧게 답하라(목록이면 항목당 한 줄). "
                "배경 설명·유의사항·중복 부연은 생략하고, 사용자가 추가로 물으면 그때 상세히 답한다."
            )
            volatile_system_prompt = (
                f"{volatile_system_prompt}\n{brevity}" if volatile_system_prompt else brevity
            )

        # needs_planning 판단
        needs_planning = self._determine_needs_planning(
            question_type, profile,
        )

        return ExecutionPlan(
            mode=mode,
            scope=scope,
            tool_groups=tool_groups,
            system_prompt=cacheable_system_prompt,
            volatile_system_prompt=volatile_system_prompt,
            guardrail_chain=profile.guardrails,
            question_type=question_type,
            strategy=strategy,
            workflow_id=workflow_id,
            workflow_step=workflow_step,
            context_adapter=profile.context_adapter,
            cache_padding_text=profile.cache_padding_text,
            profile_id=profile.id,
            rag_min_rerank_score=profile.rag_min_rerank_score,
            empty_response_fallback=profile.empty_response_fallback,
            conversation_context=conversation_context,
            response_policy=profile.response_policy,
            max_tool_calls=profile.max_tool_calls,
            agent_timeout_seconds=profile.agent_timeout_seconds,
            external_context=external_context,
            needs_planning=needs_planning,
            # P0-2/3: Profile 모델 별칭을 plan에 전달 (raw alias; 해석은 executor에서).
            # strategy_builder 는 alias를 전달만 함 — resolver/factory import 금지.
            main_model=profile.main_model,
            router_model=profile.router_model,
            max_output_tokens=profile.max_output_tokens,
        )

    @staticmethod
    def _sanitize_history(history: List[dict]) -> List[dict]:
        """대화 히스토리에서 PII 패턴을 마스킹한다."""
        from src.locale.bundle import get_locale
        pii_rules = get_locale().pii_result_guard
        sanitized = []
        for turn in history:
            content = turn.get("content", "")
            for pat, replacement in pii_rules:
                content = pat.sub(replacement, content)
            sanitized.append({**turn, "content": content})
        return sanitized

    @staticmethod
    def _determine_needs_planning(
        question_type: QuestionType,
        profile: AgentProfile,
    ) -> bool:
        """Planner 실행 필요 여부를 판단한다.

        스킵 조건:
          - planner_enabled == False (글로벌 킬스위치)
          - profile.planning_disabled == True
          - QuestionType이 GREETING 또는 SYSTEM_META (단순 응답)
        """
        if not settings.planner_enabled:
            return False

        if getattr(profile, "planning_disabled", False):
            return False

        if question_type in (QuestionType.GREETING, QuestionType.SYSTEM_META):
            return False

        return True

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
