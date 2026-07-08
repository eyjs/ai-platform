"""위임 상한(캡) 관리 (P0-6).

RAG 재시도 캡과 동일한 철학으로, 위임 횟수/깊이에 상한을 걸어 무한 위임을
원천 차단한다. budget은 메인(Supervisor 루프)만 소유·소비하며 서브에는
전달하지 않는다(§0-5 hub 강제).

**2-depth 미허용(§4.4 OUT OF SCOPE)**: 이 모듈은 자식 budget을 생성하는
API(`child()` 등)를 의도적으로 제공하지 않는다. 1-depth를 넘는 위임 구조가
필요해지면 P1에서 별도로 설계한다.
"""

from __future__ import annotations

from src.supervisor.models import SupervisorLimits


class DelegationBudget:
    """위임 횟수/깊이 캡을 강제하는 소비형 예산 객체.

    메인 루프는 위임 전 반드시 `can_delegate()`로 선검사한 뒤 실제로
    위임을 실행하고 `consume()`으로 소비를 기록해야 한다.
    """

    def __init__(self, limits: SupervisorLimits, depth: int = 0) -> None:
        self._count = 0
        self._depth = depth
        self._limits = limits

    def can_delegate(self) -> bool:
        """남은 횟수와 깊이 제한을 모두 만족해야 위임 가능."""
        return self._count < self._limits.max_delegations and self._depth < self._limits.max_depth

    def consume(self) -> None:
        """위임 1건을 소비 처리한다.

        `can_delegate()`가 False인 상태에서 호출되면 방어적으로
        `RuntimeError`를 발생시킨다. 루프는 항상 선검사 후 호출해야 한다.
        """
        if not self.can_delegate():
            raise RuntimeError("DelegationBudget 초과: can_delegate() 선검사 없이 consume()이 호출되었습니다.")
        self._count += 1

    def remaining(self) -> int:
        """남은 위임 가능 횟수(음수 방지)."""
        return max(0, self._limits.max_delegations - self._count)
