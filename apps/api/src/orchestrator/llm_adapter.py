"""Orchestrator LLM 어댑터: Tier 3 최후 수단.

OpenAI / Anthropic API를 통한 Function Calling으로 프로필을 선택한다.
Tier 1(패턴), Tier 2(키워드 스코어링)에서 해결되지 않은 질문만 도달한다.
"""

from __future__ import annotations

import json
from typing import Any

from src.config import settings
from src.locale.bundle import get_locale
from src.observability.logging import get_logger
from src.orchestrator.prompts import (
    _build_orchestrator_tools,
    get_profile_list_template,
    get_system_prompt,
    format_history,
    format_profile_list,
)

logger = get_logger(__name__)


class OrchestratorLLM:
    """오케스트레이터 전용 LLM 어댑터.

    OpenAI 호환 API의 Function Calling을 사용하여 프로필을 선택한다.
    MLX, Ollama, OpenAI, Anthropic을 지원한다.
    """

    def __init__(
        self,
        provider: str = "mlx",
        model: str = "mlx-community/Qwen2.5-7B-Instruct-4bit",
        api_key: str = "",
        timeout: float = 30.0,
        server_url: str = "",
        ollama_host: str = "http://localhost:11434",
    ):
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._server_url = server_url
        self._ollama_host = ollama_host
        self._client: Any = None

    async def initialize(self) -> None:
        """LLM 클라이언트를 초기화한다."""
        if self._provider in ("openai", "ollama", "mlx"):
            try:
                from openai import AsyncOpenAI

                if self._provider == "mlx":
                    if not self._server_url:
                        raise ValueError(
                            "MLX provider에는 server_url이 필수입니다 "
                            "(예: http://localhost:8105)"
                        )
                    self._client = AsyncOpenAI(
                        base_url=f"{self._server_url}/v1",
                        api_key="mlx",
                        timeout=self._timeout,
                    )
                elif self._provider == "ollama":
                    self._client = AsyncOpenAI(
                        base_url=f"{self._ollama_host}/v1",
                        api_key="ollama",
                        timeout=self._timeout,
                    )
                else:
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
            {"function": "no_tool_call", "text": "...", "profile_id": "..."}
        """
        if not self._client:
            raise RuntimeError("Orchestrator LLM이 초기화되지 않았습니다")

        profiles_text = format_profile_list(profiles)
        history_text = format_history(history)

        user_message = get_profile_list_template().format(
            profiles=profiles_text,
            turn_count=len(history),
            history=history_text,
            question=question,
        )

        valid_ids = {p["id"] for p in profiles}

        if self._provider in ("openai", "ollama", "mlx"):
            return await self._call_openai(user_message, valid_ids)
        elif self._provider == "anthropic":
            return await self._call_anthropic(user_message, valid_ids)

        raise RuntimeError(f"지원하지 않는 프로바이더: {self._provider}")

    async def _call_openai(self, user_message: str, valid_ids: set[str]) -> dict:
        """OpenAI Function Calling."""
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": get_system_prompt()},
                {"role": "user", "content": user_message},
            ],
            tools=_build_orchestrator_tools(),
            tool_choice="required",
            temperature=0.0,
        )

        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason

        logger.debug(
            "orchestrator_llm_response",
            has_tool_calls=bool(message.tool_calls),
            finish_reason=finish_reason,
            model=self._model,
            content_preview=(message.content or "")[:100],
        )

        if not message.tool_calls:
            # MLX 서버가 tool_choice를 무시한 경우 — 텍스트 추출 시도하지 않음
            # 텍스트에서 프로필 ID를 추출하면 설명문 속 ID가 잘못 매칭될 위험
            logger.warning(
                "orchestrator_no_tool_calls",
                content_preview=(message.content or "")[:100],
            )
            return {
                "function": "no_tool_call",
                "text": message.content or "",
                "profile_id": "",
                "reason": "tool_calls 없음",
            }

        tool_call = message.tool_calls[0]
        fn_name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                "orchestrator_json_parse_error",
                raw=tool_call.function.arguments[:200],
                error=str(e),
            )
            return {
                "function": "no_tool_call",
                "text": tool_call.function.arguments or "",
                "profile_id": "",
                "reason": f"JSON 파싱 실패: {e}",
            }

        if fn_name == "select_profile":
            return {
                "function": "select_profile",
                "profile_id": args.get("profile_id", ""),
                "reason": args.get("reason", ""),
            }

        # 예상치 못한 function name
        return {
            "function": "no_tool_call",
            "text": str(args),
            "profile_id": "",
            "reason": f"예상치 못한 function: {fn_name}",
        }

    async def _call_anthropic(self, user_message: str, valid_ids: set[str]) -> dict:
        """Anthropic Tool Use."""
        anthropic_tools = [
            {
                "name": "select_profile",
                "description": get_locale().prompt(
                    "orchestrator_tool_description",
                    fallback_profile_id=settings.fallback_profile_id,
                ),
                "input_schema": {
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
        ]

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=get_system_prompt(),
            messages=[{"role": "user", "content": user_message}],
            tools=anthropic_tools,
            tool_choice={"type": "any"},
        )

        for block in response.content:
            if block.type == "tool_use":
                if block.name == "select_profile":
                    return {
                        "function": "select_profile",
                        "profile_id": block.input.get("profile_id", ""),
                        "reason": block.input.get("reason", ""),
                    }

        # Anthropic이 tool을 호출하지 않은 경우 — 텍스트 추출 시도하지 않음
        text_content = ""
        for block in response.content:
            if block.type == "text":
                text_content += block.text

        logger.warning(
            "orchestrator_no_tool_calls",
            content_preview=text_content[:100],
        )
        return {
            "function": "no_tool_call",
            "text": text_content,
            "profile_id": "",
            "reason": "Anthropic tool 미호출",
        }

    async def close(self) -> None:
        """리소스 정리."""
        if self._client and hasattr(self._client, "close"):
            await self._client.close()
        self._client = None
