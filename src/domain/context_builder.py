"""Context Builder: 토큰 예산 기반 맥락 구성.

검색 결과 + 대화 이력을 토큰 예산 내에서 조합.
"""

import logging
from typing import List

import tiktoken

logger = logging.getLogger(__name__)


class ConversationContextBuilder:
    """토큰 예산 기반 맥락 구성기."""

    def __init__(self, token_budget: int = 2000):
        self._budget = token_budget
        self._encoding = tiktoken.get_encoding("cl100k_base")

    def build(
        self,
        search_results: List[dict],
        conversation_history: List[dict],
        question: str,
    ) -> str:
        """토큰 예산 내에서 검색 결과 + 대화 이력을 조합한다."""
        parts = []
        remaining = self._budget

        # 1. 질문 (필수)
        q_tokens = len(self._encoding.encode(question))
        remaining -= q_tokens

        # 2. 검색 결과 (우선순위 높음)
        if search_results:
            for i, result in enumerate(search_results):
                content = result.get("content", "")
                title = result.get("title", result.get("file_name", ""))
                text = f"[{i+1}] {title}\n{content}"
                tokens = len(self._encoding.encode(text))
                if tokens > remaining:
                    # 잘라서 넣기
                    truncated = self._truncate(text, remaining)
                    if truncated:
                        parts.append(truncated)
                    break
                parts.append(text)
                remaining -= tokens

        # 3. 대화 이력 (남은 예산으로)
        if conversation_history and remaining > 100:
            history_parts = []
            for turn in reversed(conversation_history[-5:]):
                text = f"{turn['role']}: {turn['content']}"
                tokens = len(self._encoding.encode(text))
                if tokens > remaining:
                    break
                history_parts.insert(0, text)
                remaining -= tokens
            if history_parts:
                parts.insert(0, "대화 이력:\n" + "\n".join(history_parts))

        return "\n\n".join(parts)

    def _truncate(self, text: str, max_tokens: int) -> str:
        tokens = self._encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return self._encoding.decode(tokens[:max_tokens])
