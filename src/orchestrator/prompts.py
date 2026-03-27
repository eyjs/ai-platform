"""Orchestrator LLM 프롬프트 + Function 정의.

Tier 3 전용: Tier 1(패턴), Tier 2(키워드 스코어링)에서 해결되지 않은
질문만 LLM에 도달한다. general_response tool은 제거되었으며,
반드시 프로필을 선택해야 한다.
"""

from __future__ import annotations

from src.config import settings
from src.locale.bundle import get_locale


def get_system_prompt() -> str:
    """Orchestrator 시스템 프롬프트를 반환한다."""
    return get_locale().prompt(
        "orchestrator_system",
        fallback_profile_id=settings.fallback_profile_id,
    )


def get_profile_list_template() -> str:
    """프로필 목록 템플릿을 반환한다."""
    return get_locale().prompt("orchestrator_profile_list")


def format_profile_list(profiles: list[dict]) -> str:
    """프로필 목록을 LLM 입력용 텍스트로 변환한다."""
    lines = []
    for p in profiles:
        domains = ", ".join(p.get("domain_scopes", [])) or get_locale().label("domain_all")
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
        return get_locale().label("history_none")
    recent = turns[-max_turns:]
    lines = []
    for t in recent:
        role = get_locale().label("role_user") if t.get("role") == "user" else get_locale().label("role_assistant")
        content = t.get("content", "")[:200]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _build_orchestrator_tools() -> list[dict]:
    """OpenAI Function Calling 도구 정의를 생성한다."""
    return [
        {
            "type": "function",
            "function": {
                "name": "select_profile",
                "description": get_locale().prompt(
                    "orchestrator_tool_description",
                    fallback_profile_id=settings.fallback_profile_id,
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "profile_id": {
                            "type": "string",
                            "description": get_locale().prompt("orchestrator_tool_profile_id_desc"),
                        },
                        "reason": {
                            "type": "string",
                            "description": get_locale().prompt("orchestrator_tool_reason_desc"),
                        },
                    },
                    "required": ["profile_id", "reason"],
                },
            },
        },
    ]
