"""의미 분류 도메인 타입.

분류 후보(Candidate)와 결과(ClassifyResult)는 라우터(SemanticClassifier 구현)와
워크플로우 엔진(분기 분류 호출부)이 공유하는 순수 데이터 타입이다.

이 타입들을 도메인 레이어에 두는 이유: 워크플로우 엔진(하위 레이어)이 분류 후보를
구성할 때 라우터(상위 레이어)를 import하면 단방향 의존(Gateway→Router→Agent→Tool)을
역행한다. 공유 타입을 도메인으로 내려 양쪽이 아래로만 의존하도록 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Candidate:
    """분류 후보. label은 반환 식별자(workflow_id/intent/branch 옵션), description은 의미판단용."""

    label: str
    description: str = ""


@dataclass
class ClassifyResult:
    label: Optional[str]
    confidence: float = 0.0
