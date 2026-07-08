"""DelegationBudget 단위 테스트 (task-006, P0-6)."""

import pytest

from src.supervisor.limits import DelegationBudget
from src.supervisor.models import SupervisorLimits


def test_max_delegations_exhausted_blocks_further_delegation():
    """max_delegations=2 → consume 2회 후 can_delegate() False, remaining()==0."""
    budget = DelegationBudget(SupervisorLimits(max_delegations=2, max_depth=1))

    assert budget.can_delegate() is True
    budget.consume()
    assert budget.can_delegate() is True
    budget.consume()

    assert budget.can_delegate() is False
    assert budget.remaining() == 0


def test_depth_exceeding_max_depth_blocks_immediately():
    """max_depth=1, depth=1로 생성 → 즉시 can_delegate() False (1-depth 초과 차단)."""
    budget = DelegationBudget(SupervisorLimits(max_delegations=4, max_depth=1), depth=1)

    assert budget.can_delegate() is False
    # 위임 횟수는 남아있어도 깊이 제한으로 차단되어야 한다.
    assert budget.remaining() == 4


def test_consume_without_can_delegate_raises_runtime_error():
    """can_delegate() False 상태에서 consume() 호출 시 RuntimeError."""
    budget = DelegationBudget(SupervisorLimits(max_delegations=1, max_depth=1))

    budget.consume()
    assert budget.can_delegate() is False

    with pytest.raises(RuntimeError):
        budget.consume()


def test_no_child_budget_api_exists():
    """child()류 2-depth 생성 API가 존재하지 않음을 hasattr 부재로 검증(§4.4)."""
    budget = DelegationBudget(SupervisorLimits())

    assert not hasattr(budget, "child")
    assert not hasattr(DelegationBudget, "child")
