"""Orchestrator LLM 프롬프트 + Function 정의.

Tier 3 전용: Tier 1(패턴), Tier 2(키워드 스코어링)에서 해결되지 않은
질문만 LLM에 도달한다. general_response tool은 제거되었으며,
반드시 프로필을 선택해야 한다.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
당신은 사용자 질문을 적절한 전문 에이전트(프로필)에게 라우팅하는 오케스트레이터입니다.

규칙:
1. 반드시 select_profile 도구를 호출하여 프로필을 선택하세요.
2. 인사, 잡담, 일반 대화도 general-chat 또는 general-assistant 프로필로 보내세요.
3. 어떤 질문이든 가장 관련성 높은 프로필을 선택하세요. "해당 없음"은 불가합니다.
4. 대화 이력을 참고하여 맥락을 유지하세요.
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


# OpenAI Function Calling 도구 정의 — select_profile만 제공
ORCHESTRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "select_profile",
            "description": (
                "사용자 질문에 가장 적합한 프로필을 선택한다. "
                "인사/잡담도 general-chat으로 라우팅한다."
            ),
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
]
