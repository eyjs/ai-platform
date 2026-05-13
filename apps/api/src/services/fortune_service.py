"""Fortune 해석 서비스.

사주 운세 해석 LLM 호출 + 후처리:
- 3종 운세 타입 (today/yearly/tojeong)
- 절단 JSON 복구
- 한국어 줄바꿈 삽입
"""

import json
import re
from typing import Any, Dict, Optional

from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.services.fortune_prompts import (
    FORTUNE_SYSTEM_PROMPT,
    build_today_prompt,
    build_tojeong_prompt,
    build_yearly_prompt,
)

logger = get_logger(__name__)

_PROMPT_BUILDERS = {
    "today": build_today_prompt,
    "yearly": build_yearly_prompt,
    "tojeong": build_tojeong_prompt,
}

_LINEBREAK_PATTERN = re.compile(r"([다요죠][\.!]) ")


class FortuneService:
    """사주 운세 해석 서비스."""

    def __init__(self, main_llm: LLMProvider):
        self._llm = main_llm

    async def interpret(
        self,
        fortune_type: str,
        saju_context: str,
        tojeong_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        builder = _PROMPT_BUILDERS.get(fortune_type)
        if builder is None:
            raise ValueError(f"지원하지 않는 운세 타입: {fortune_type}")

        prompt = builder(saju_context)

        logger.info("fortune_interpret_start", fortune_type=fortune_type)

        raw = await self._llm.generate(prompt, system=FORTUNE_SYSTEM_PROMPT)

        cleaned = _recover_truncated_json(raw)
        data = json.loads(cleaned)
        data = _add_line_breaks(data)

        logger.info("fortune_interpret_done", fortune_type=fortune_type)
        return data


def _recover_truncated_json(raw: str) -> str:
    """절단된 JSON을 복구한다.

    LLM이 JSON 뒤에 중국어/한자 텍스트를 붙이거나
    토큰 한도로 잘리는 경우를 처리.
    """
    start = raw.find("{")
    if start == -1:
        return raw

    text = raw[start:]

    last_brace = text.rfind("}")
    if last_brace != -1:
        tail = text[last_brace + 1:].strip()
        if tail:
            text = text[:last_brace + 1]

    stack: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in ("{", "["):
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    for opener in reversed(stack):
        text += "}" if opener == "{" else "]"

    return text


def _add_line_breaks(obj: Any) -> Any:
    """모든 문자열 값에 한국어 줄바꿈을 재귀적으로 삽입한다."""
    if isinstance(obj, str):
        return _insert_line_breaks(obj)
    if isinstance(obj, dict):
        return {k: _add_line_breaks(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_add_line_breaks(item) for item in obj]
    return obj


def _insert_line_breaks(text: str) -> str:
    """한국어 문장 종결(다/요/죠 + 구두점) 뒤에 줄바꿈을 삽입한다."""
    if "\n" in text:
        return text
    return _LINEBREAK_PATTERN.sub(r"\1\n", text)
