"""Orchestrator LLM 어댑터: 최상위 모델 호출.

OpenAI / Anthropic API를 통한 Function Calling으로 프로필을 선택한다.
"""

from __future__ import annotations

import json
from typing import Any

from src.observability.logging import get_logger
from src.orchestrator.prompts import (
    ORCHESTRATOR_TOOLS,
    PROFILE_LIST_TEMPLATE,
    SYSTEM_PROMPT,
    format_history,
    format_profile_list,
)

logger = get_logger(__name__)


class OrchestratorLLM:
    """오케스트레이터 전용 LLM 어댑터.

    OpenAI API의 Function Calling을 사용하여 프로필 선택 또는 일반 응답을 결정한다.
    """

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o",
        api_key: str = "",
        timeout: float = 10.0,
    ):
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._client: Any = None

    async def initialize(self) -> None:
        """LLM 클라이언트를 초기화한다."""
        if self._provider == "openai":
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(
                    api_key=self._api_key,
                    timeout=self._timeout,
                )
            except ImportError:
                logger.warning("openai 패키지 미설치, orchestrator LLM 비활성")
        elif self._provider == "anthropic":
            try:
                from anthropic import AsyncAnthropic
                self._client = AsyncAnthropic(
                    api_key=self._api_key,
                    timeout=self._timeout,
                )
            except ImportError:
                logger.warning("anthropic 패키지 미설치, orchestrator LLM 비활성")

    async def select_profile(
        self,
        question: str,
        profiles: list[dict],
        history: list[dict],
    ) -> dict:
        """LLM Function Calling으로 프로필을 선택한다.

        Returns:
            {"function": "select_profile", "profile_id": "...", "reason": "..."}
            또는
            {"function": "general_response", "message": "..."}
        """
        if not self._client:
            raise RuntimeError("Orchestrator LLM이 초기화되지 않았습니다")

        profiles_text = format_profile_list(profiles)
        history_text = format_history(history)

        user_message = PROFILE_LIST_TEMPLATE.format(
            profiles=profiles_text,
            turn_count=len(history),
            history=history_text,
            question=question,
        )

        if self._provider == "openai":
            return await self._call_openai(user_message)
        elif self._provider == "anthropic":
            return await self._call_anthropic(user_message)

        raise RuntimeError(f"지원하지 않는 프로바이더: {self._provider}")

    async def _call_openai(self, user_message: str) -> dict:
        """OpenAI Function Calling."""
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            tools=ORCHESTRATOR_TOOLS,
            tool_choice="required",
            temperature=0.0,
        )

        message = response.choices[0].message
        if not message.tool_calls:
            return {
                "function": "general_response",
                "message": message.content or "무엇을 도와드릴까요?",
            }

        tool_call = message.tool_calls[0]
        fn_name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)

        if fn_name == "select_profile":
            return {
                "function": "select_profile",
                "profile_id": args["profile_id"],
                "reason": args.get("reason", ""),
            }
        elif fn_name == "general_response":
            return {
                "function": "general_response",
                "message": args.get("message", ""),
            }

        return {"function": "general_response", "message": "무엇을 도와드릴까요?"}

    async def _call_anthropic(self, user_message: str) -> dict:
        """Anthropic Tool Use."""
        # Anthropic 형식의 tool 정의
        anthropic_tools = [
            {
                "name": "select_profile",
                "description": "사용자 질문에 가장 적합한 프로필을 선택한다.",
                "input_schema": {
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
            {
                "name": "general_response",
                "description": "인사, 잡담 등 프로필이 필요 없는 질문에 직접 응답한다.",
                "input_schema": {
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
        ]

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            tools=anthropic_tools,
            tool_choice={"type": "any"},
        )

        for block in response.content:
            if block.type == "tool_use":
                if block.name == "select_profile":
                    return {
                        "function": "select_profile",
                        "profile_id": block.input["profile_id"],
                        "reason": block.input.get("reason", ""),
                    }
                elif block.name == "general_response":
                    return {
                        "function": "general_response",
                        "message": block.input.get("message", ""),
                    }

        return {"function": "general_response", "message": "무엇을 도와드릴까요?"}

    async def close(self) -> None:
        """리소스 정리."""
        if self._client and hasattr(self._client, "close"):
            await self._client.close()
        self._client = None
