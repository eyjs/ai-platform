"""Safety Guard: 모든 응답에 적용되는 안전 장치.

Profile.guardrails 설정에 따라 동적으로 체인 구성.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


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

    @classmethod
    def passed(cls) -> GuardrailResult:
        return cls(action="pass")

    @classmethod
    def warn(cls, reason: str, modified_answer: str) -> GuardrailResult:
        return cls(action="warn", reason=reason, modified_answer=modified_answer)

    @classmethod
    def block(cls, reason: str) -> GuardrailResult:
        return cls(action="block", reason=reason)


@runtime_checkable
class Guardrail(Protocol):
    """가드레일 프로토콜."""

    name: str

    async def check(
        self,
        answer: str,
        context: GuardrailContext,
    ) -> GuardrailResult: ...
