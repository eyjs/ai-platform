"""Planner 노드: Plan-and-Execute 아키텍처의 실행 계획 수립기.

질문과 사용 가능한 도구 목록을 LLM에 전달하여 실행 단계(steps)를 생성한다.
Planner는 "무엇을 검색할지"만 결정. "어떤 데이터에서 검색할지"는
SearchScope이 execute_tools에서 자동 주입.

프로필 격리 원칙:
  - Planner가 보는 것: 질문 텍스트, 도구 이름/설명, 대화 맥락 요약
  - Planner가 보지 않는 것: RAG 검색 결과, 문서 내용, tenant_id
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable, Union

from src.agent.state import AgentState
from src.config import settings
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.tools.base import ScopedTool, Tool

logger = get_logger(__name__)

PLANNER_SYSTEM_PROMPT = """당신은 AI 어시스턴트의 실행 계획 수립기입니다.
사용자 질문을 분석하고, 사용 가능한 도구를 활용한 최적의 실행 단계를 계획하세요.

규칙:
- 같은 group 번호의 step은 병렬 실행됩니다
- group 번호가 다르면 순차 실행됩니다 (낮은 번호 먼저)
- 단순 질문은 step 1개로 충분합니다
- 비교/대조 질문은 여러 검색을 병렬로 계획하세요
- step_id는 영문 snake_case로 작성하세요
- tool은 사용 가능한 도구 이름 중 하나를 정확히 사용하세요
- params는 해당 도구의 입력 스키마에 맞게 작성하세요"""

PLANNER_USER_TEMPLATE = """사용 가능한 도구:
{tool_descriptions}

{context_section}질문: {question}

JSON 형식으로 응답하세요:
{{
  "steps": [
    {{"step_id": "string", "tool": "tool_name", "params": {{"query": "..."}}, "group": 1}}
  ],
  "reasoning": "판단 근거"
}}"""


def _build_tool_descriptions(tools: list[Union[Tool, ScopedTool]]) -> str:
    """도구 목록을 Planner 프롬프트용 텍스트로 변환한다."""
    parts = []
    for tool in tools:
        desc = f"- {tool.name}: {tool.description}"
        if tool.input_schema:
            schema_str = json.dumps(tool.input_schema, ensure_ascii=False)
            desc += f"\n  입력 스키마: {schema_str}"
        parts.append(desc)
    return "\n".join(parts)


def _validate_steps(steps: list[dict], available_tool_names: set[str]) -> list[dict]:
    """Planner 출력의 steps를 검증하고 정제한다."""
    valid_steps = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        tool = step.get("tool", "")
        if tool not in available_tool_names:
            logger.warning("planner_invalid_tool", tool=tool, available=list(available_tool_names))
            continue
        valid_steps.append({
            "step_id": step.get("step_id", f"step_{len(valid_steps) + 1}"),
            "tool": tool,
            "params": step.get("params", {}),
            "group": step.get("group", 1),
        })
    return valid_steps


def create_planner(
    llm: LLMProvider,
    tool_resolver: Callable[[list[str]], list[Union[Tool, ScopedTool]]],
) -> Callable:
    """Planner 노드 팩토리.

    Args:
        llm: LLM 프로바이더 (generate_json 사용)
        tool_resolver: 도구 이름 -> 도구 인스턴스 해석 함수 (ToolRegistry.resolve)
    """

    async def plan_execution(state: AgentState) -> dict:
        plan = state["plan"]

        # Planner 스킵 조건
        if not plan.needs_planning:
            return {}

        if not settings.planner_enabled:
            logger.info("planner_disabled_by_config")
            return {}

        # 사용 가능한 도구 목록 추출
        tool_names = list({tc.tool_name for group in plan.tool_groups for tc in group})
        if not tool_names:
            return {}

        tools = tool_resolver(tool_names)
        if not tools:
            return {}

        available_tool_names = {t.name for t in tools}
        tool_descriptions = _build_tool_descriptions(tools)

        # 대화 맥락 섹션
        context_section = ""
        if plan.conversation_context:
            context_section = f"대화 맥락:\n{plan.conversation_context}\n\n"

        prompt = PLANNER_USER_TEMPLATE.format(
            tool_descriptions=tool_descriptions,
            context_section=context_section,
            question=state["question"],
        )

        try:
            result = await asyncio.wait_for(
                llm.generate_json(prompt, system=PLANNER_SYSTEM_PROMPT),
                timeout=settings.planner_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("planner_timeout", timeout=settings.planner_timeout)
            return {}
        except Exception as e:
            logger.warning("planner_llm_error", error=str(e))
            return {}

        steps = result.get("steps", [])
        reasoning = result.get("reasoning", "")

        if not steps:
            logger.info("planner_empty_steps")
            return {}

        valid_steps = _validate_steps(steps, available_tool_names)
        if not valid_steps:
            logger.warning("planner_no_valid_steps")
            return {}

        logger.info(
            "planner_success",
            steps_count=len(valid_steps),
            reasoning=reasoning[:100],
        )

        return {
            "planned_steps": valid_steps,
            "planning_reasoning": reasoning,
        }

    return plan_execution
