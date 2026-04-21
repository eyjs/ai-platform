"""Safety Guard: 모든 응답에 적용되는 안전 장치.

Profile.guardrails 설정에 따라 동적으로 체인 구성.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class GuardrailContext:
    """가드레일 실행 시 참조하는 맥락."""

    question: str = ""
    source_documents: list = field(default_factory=list)
    fact_chains: list = field(default_factory=list)
    profile_id: str = ""
    response_policy: str = "balanced"


@dataclass(frozen=True)
class GuardrailResult:
    """가드레일 판정 결과."""

    action: str  # "pass" | "warn" | "block"
    reason: str | None = None
    modified_answer: str | None = None
    # Task 014: 수치화된 품질 스코어 (0.0~1.0). 측정 불가 시 None.
    # Faithfulness guard 가 주로 사용하며, 다른 guard 는 None 유지 가능 (하위호환).
    score: Optional[float] = None

    @classmethod
    def passed(cls, score: Optional[float] = None) -> GuardrailResult:
        return cls(action="pass", score=score)

    @classmethod
    def warn(
        cls,
        reason: str,
        modified_answer: str | None,
        score: Optional[float] = None,
    ) -> GuardrailResult:
        return cls(
            action="warn",
            reason=reason,
            modified_answer=modified_answer,
            score=score,
        )

    @classmethod
    def block(cls, reason: str, score: Optional[float] = None) -> GuardrailResult:
        return cls(action="block", reason=reason, score=score)


@runtime_checkable
class Guardrail(Protocol):
    """가드레일 프로토콜."""

    name: str

    async def check(
        self,
        answer: str,
        context: GuardrailContext,
    ) -> GuardrailResult: ...
