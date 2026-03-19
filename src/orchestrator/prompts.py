"""Orchestrator LLM 프롬프트 + Function 정의."""

from __future__ import annotations

SYSTEM_PROMPT = """\
당신은 사용자 질문을 적절한 전문 에이전트(프로필)에게 라우팅하는 오케스트레이터입니다.

사용 가능한 프로필 목록이 주어지면, 사용자의 질문 의도를 분석하여:
1. 가장 적합한 프로필을 선택하거나
2. 인사/잡담 등 프로필이 필요 없는 경우 직접 응답합니다.

대화 이력을 참고하여 맥락을 유지하세요.
"""

PROFILE_LIST_TEMPLATE = """\
사용 가능한 프로필:
{profiles}

대화 이력 (최근 {turn_count}턴):
{history}

사용자 질문: {question}
"""


def format_profile_list(profiles: list[dict]) -> str:
    """프로필 목록을 LLM 입력용 텍스트로 변환한다."""
    lines = []
    for p in profiles:
        domains = ", ".join(p.get("domain_scopes", [])) or "전체"
        hints = ""
        for h in p.get("intent_hints", []):
            hints += f" [{h.get('name', '')}: {h.get('description', '')}]"
        lines.append(
            f"- id: {p['id']} | 이름: {p['name']} | 설명: {p.get('description', '')} "
            f"| 도메인: {domains}{hints}"
        )
    return "\n".join(lines)


def format_history(turns: list[dict], max_turns: int = 5) -> str:
    """대화 이력을 텍스트로 변환한다."""
    if not turns:
        return "(없음)"
    recent = turns[-max_turns:]
    lines = []
    for t in recent:
        role = "사용자" if t.get("role") == "user" else "어시스턴트"
        content = t.get("content", "")[:200]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


# OpenAI Function Calling 도구 정의
ORCHESTRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "select_profile",
            "description": "사용자 질문에 가장 적합한 프로필을 선택한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "profile_id": {
                        "type": "string",
                        "description": "선택할 프로필 ID",
                    },
                    "reason": {
                        "type": "string",
                        "description": "선택 이유 (한국어, 1문장)",
                    },
                },
                "required": ["profile_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "general_response",
            "description": "인사, 잡담 등 프로필이 필요 없는 질문에 직접 응답한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "사용자에게 보낼 응답 메시지",
                    },
                },
                "required": ["message"],
            },
        },
    },
]
