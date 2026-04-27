"""MemoryExtractor: 대화에서 메모리 사실 추출.

패턴 매칭 기반으로 대화 턴에서 사실, 선호도, 반복 주제 등을 추출.
LLM 기반 추출은 향후 확장점으로 인터페이스만 준비.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class MemoryExtractor:
    """대화 턴에서 메모리 사실을 추출하는 서비스."""

    def __init__(self):
        self._name_patterns = [
            r"제 이름은 (.+?)입니다",
            r"저는 (.+?)라고 합니다",
            r"(.+?)라고 불러주세요",
            r"(.+?)입니다",  # 단순 자기소개
        ]
        self._birth_patterns = [
            r"(\d{4})년 (\d{1,2})월 (\d{1,2})일 생",
            r"생년월일.*?(\d{4})[년.-](\d{1,2})[월.-](\d{1,2})",
            r"(\d{4})[년.-](\d{1,2})[월.-](\d{1,2})일.*?태어났",
        ]
        self._preference_patterns = [
            r"(.+?)를? 좋아합니다",
            r"(.+?)가? 취미입니다",
            r"(.+?)에 관심이 있습니다",
            r"(.+?)를? 싫어합니다",
            r"(.+?)는? 별로입니다",
        ]
        self._topic_patterns = [
            r"(.+?)에 대해 자주 물어보",
            r"(.+?) 관련해서 계속",
            r"(.+?) 이야기를 많이",
        ]

    def extract_facts(self, conversation_turns: list[dict]) -> list[dict]:
        """대화 턴에서 사실 추출.

        Args:
            conversation_turns: 대화 턴 목록
                [{"role": "user", "content": "...", "timestamp": "..."}, ...]

        Returns:
            추출된 사실 목록
                [{"key": "name", "value": "홍길동", "memory_type": "identity", "confidence": 0.8}, ...]
        """
        facts = []

        for turn in conversation_turns:
            if turn.get("role") != "user":
                continue

            content = turn.get("content", "").strip()
            if not content:
                continue

            # 이름 추출
            name_facts = self._extract_names(content)
            facts.extend(name_facts)

            # 생년월일 추출
            birth_facts = self._extract_birth_dates(content)
            facts.extend(birth_facts)

            # 선호도 추출
            preference_facts = self._extract_preferences(content)
            facts.extend(preference_facts)

            # 반복 주제 추출
            topic_facts = self._extract_topics(content)
            facts.extend(topic_facts)

        # 중복 제거 (같은 key + value 조합)
        facts = self._deduplicate_facts(facts)

        logger.debug(
            "memory_extraction_completed",
            extra={
                "turns_processed": len(conversation_turns),
                "facts_extracted": len(facts),
            },
        )

        return facts

    def _extract_names(self, content: str) -> list[dict]:
        """이름 추출."""
        facts = []
        for pattern in self._name_patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                name = match.group(1).strip()
                if len(name) >= 2 and len(name) <= 20:  # 합리적인 이름 길이
                    facts.append({
                        "key": "name",
                        "value": name,
                        "memory_type": "identity",
                        "confidence": 0.8,
                        "extracted_at": datetime.now(timezone.utc).isoformat(),
                    })
        return facts

    def _extract_birth_dates(self, content: str) -> list[dict]:
        """생년월일 추출."""
        facts = []
        for pattern in self._birth_patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                try:
                    year = int(match.group(1))
                    month = int(match.group(2))
                    day = int(match.group(3))

                    # 기본 유효성 검사
                    if 1900 <= year <= datetime.now().year and 1 <= month <= 12 and 1 <= day <= 31:
                        birth_date = f"{year:04d}-{month:02d}-{day:02d}"
                        facts.append({
                            "key": "birth_date",
                            "value": birth_date,
                            "memory_type": "identity",
                            "confidence": 0.9,
                            "extracted_at": datetime.now(timezone.utc).isoformat(),
                        })
                except (ValueError, IndexError):
                    continue
        return facts

    def _extract_preferences(self, content: str) -> list[dict]:
        """선호도 추출."""
        facts = []
        for pattern in self._preference_patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                preference = match.group(1).strip()
                if len(preference) >= 2 and len(preference) <= 50:
                    sentiment = "positive"
                    if any(word in pattern for word in ["싫어", "별로"]):
                        sentiment = "negative"

                    facts.append({
                        "key": f"preference_{sentiment}",
                        "value": preference,
                        "memory_type": "preference",
                        "confidence": 0.7,
                        "extracted_at": datetime.now(timezone.utc).isoformat(),
                    })
        return facts

    def _extract_topics(self, content: str) -> list[dict]:
        """반복 주제 추출."""
        facts = []
        for pattern in self._topic_patterns:
            matches = re.finditer(pattern, content, re.IGNORECASE)
            for match in matches:
                topic = match.group(1).strip()
                if len(topic) >= 2 and len(topic) <= 50:
                    facts.append({
                        "key": "frequent_topic",
                        "value": topic,
                        "memory_type": "behavior",
                        "confidence": 0.6,
                        "extracted_at": datetime.now(timezone.utc).isoformat(),
                    })
        return facts

    def _deduplicate_facts(self, facts: list[dict]) -> list[dict]:
        """중복 사실 제거."""
        seen = set()
        deduplicated = []

        for fact in facts:
            # key + value 조합으로 중복 검사
            fact_key = (fact["key"], str(fact["value"]))
            if fact_key not in seen:
                seen.add(fact_key)
                deduplicated.append(fact)

        return deduplicated

    def extract_with_llm(
        self,
        conversation_turns: list[dict],
        llm_client: Optional[object] = None,
    ) -> list[dict]:
        """LLM 기반 사실 추출 (향후 구현).

        Args:
            conversation_turns: 대화 턴 목록
            llm_client: LLM 클라이언트 (향후 확장)

        Returns:
            LLM이 추출한 사실 목록
        """
        # TODO: LLM 기반 추출 구현
        logger.info("llm_based_extraction_not_implemented")
        return []