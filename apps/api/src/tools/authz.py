"""도구 실행단 하드 인가 (F19, Step 15).

기존에는 도구 제한이 프롬프트 레벨(Profile.tools에 없는 도구는 LLM이 모름)에만
의존했다. 프롬프트 인젝션 등으로 LLM이 도구 호출을 시도해도, 실행 직전에
역할(UserRole) 기반 하드체크로 한 번 더 차단한다.

선언 방식: 도구 클래스 속성으로 최소 요구 역할을 선언한다.

    class FlowSNSTaskActionsTool:
        name = "flowsns_task_actions"
        required_role = UserRole.EDITOR  # 미선언 시 제한 없음 (읽기 전용 도구)

정책:
  - required_role 미선언(None) → 제한 없음. 기존 읽기 전용 도구의 동작 보존.
  - 선언됨 → context.user_role이 ROLE_HIERARCHY에서 같거나 높아야 실행.
  - 알 수 없는 역할 문자열(요청측/도구측 모두) → 보수적으로 거부 (fail-closed).

이 체크는 registry.execute(결정론 경로)와 tool_adapter(에이전틱 LangChain 경로)
양쪽에서 호출된다 — 에이전틱 경로는 레지스트리를 거치지 않고 도구를 직접
실행하므로 둘 다 걸어야 우회가 없다.
"""

from __future__ import annotations

import logging
from typing import Any

from src.domain.agent_context import AgentContext
from src.domain.models import ROLE_HIERARCHY

logger = logging.getLogger(__name__)


def _rank(role: Any) -> int | None:
    """역할(UserRole 멤버 또는 평문 문자열)을 위계 숫자로. 미상이면 None.

    str(UserRole.X)는 'UserRole.X'가 되므로 str() 대신 .value로 정규화한다.
    """
    return ROLE_HIERARCHY.get(getattr(role, "value", role))


def authorize_tool(tool: Any, context: AgentContext) -> str | None:
    """도구 실행 인가. 거부 사유 문자열을 반환하고, 허용이면 None.

    거부 메시지는 LLM/사용자에게 그대로 노출되므로 내부 정보 없이 명시적으로.
    """
    required = getattr(tool, "required_role", None)
    if required is None:
        return None

    required_rank = _rank(required)
    if required_rank is None:
        # 도구 메타 오류 — 알 수 없는 요구 역할은 열어주지 않는다 (fail-closed)
        logger.error(
            "tool_authz_misconfigured tool=%s required_role=%r", tool.name, required,
        )
        return f"도구 '{tool.name}'의 권한 설정이 올바르지 않아 실행할 수 없습니다"

    user_rank = _rank(context.user_role)
    if user_rank is None or user_rank < required_rank:
        required_label = getattr(required, "value", required)
        user_label = getattr(context.user_role, "value", context.user_role)
        logger.warning(
            "tool_authz_denied tool=%s required=%s user_role=%s user_id=%s",
            tool.name, required_label, user_label, context.user_id,
        )
        return (
            f"도구 '{tool.name}'은(는) {required_label} 이상 권한이 필요합니다 "
            f"(현재: {user_label or '미지정'})"
        )

    return None
