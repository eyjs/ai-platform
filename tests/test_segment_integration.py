"""Segment 접근 제어 통합 테스트.

전체 segment 접근 제어 흐름을 end-to-end로 검증한다.
- AccessPolicyStore 정책 판단
- AuthService.check_profile_access() segment 교차 검증
- MasterOrchestrator._get_available_profiles() segment 필터링
- 정책 reload 후 반영

DB 의존 없이 AccessPolicyStore._policies를 직접 주입하여 로직만 검증한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.gateway.access_policy import AccessPolicyStore
from src.gateway.auth import AuthError, AuthService
from src.gateway.models import UserContext


# ── 헬퍼 ──


def _make_policy_store(policies: dict[str, set[str]]) -> AccessPolicyStore:
    """DB 없이 _policies를 직접 주입한 AccessPolicyStore를 생성한다."""
    pool = AsyncMock()
    store = AccessPolicyStore(pool)
    store._policies = policies
    return store


@dataclass
class StubProfile:
    """MasterOrchestrator 테스트용 프로필 스텁."""

    id: str
    name: str = ""
    description: str = ""
    domain_scopes: list[str] = field(default_factory=list)
    intent_hints: list = field(default_factory=list)


def _make_orchestrator(profiles: list[StubProfile], access_policy=None):
    """테스트용 MasterOrchestrator를 생성한다."""
    from src.orchestrator.orchestrator import MasterOrchestrator

    profile_store = AsyncMock()
    profile_store.list_all = AsyncMock(return_value=profiles)

    tenant_service = AsyncMock()
    tenant_service.get_allowed_profiles = AsyncMock(return_value=[])

    return MasterOrchestrator(
        llm=MagicMock(),
        profile_store=profile_store,
        session_memory=MagicMock(),
        workflow_engine=MagicMock(),
        tenant_service=tenant_service,
        access_policy=access_policy,
    )


# ── 시나리오 1: customer 사용자가 customer 전용 Profile 접근 -> 허용 ──


@pytest.mark.asyncio
async def test_customer_accesses_customer_profile():
    """customer 사용자가 customer 전용 Profile에 접근하면 허용된다."""
    policy_store = _make_policy_store({
        "insurance-qa": {"customer"},
    })
    svc = AuthService(
        pool=MagicMock(),
        auth_required=True,
        access_policy=policy_store,
    )

    user = UserContext(user_id="c1", user_type="customer")

    # 예외 없이 통과해야 한다
    await svc.check_profile_access(user, "insurance-qa")


# ── 시나리오 2: customer 사용자가 staff 전용 Profile 접근 -> AuthError ──


@pytest.mark.asyncio
async def test_customer_denied_staff_profile():
    """customer 사용자가 staff 전용 Profile에 접근하면 AuthError가 발생한다."""
    policy_store = _make_policy_store({
        "hr-bot": {"staff"},
    })
    svc = AuthService(
        pool=MagicMock(),
        auth_required=True,
        access_policy=policy_store,
    )

    user = UserContext(user_id="c2", user_type="customer")

    with pytest.raises(AuthError, match="사용자군 'customer'"):
        await svc.check_profile_access(user, "hr-bot")


# ── 시나리오 3: 정책 미설정 Profile은 모든 user_type에 공개 ──


@pytest.mark.asyncio
async def test_no_policy_allows_all():
    """정책이 설정되지 않은 Profile은 어떤 user_type이든 접근을 허용한다."""
    # hr-bot에만 정책 있음, general-chat에는 정책 없음
    policy_store = _make_policy_store({
        "hr-bot": {"staff"},
    })
    svc = AuthService(
        pool=MagicMock(),
        auth_required=True,
        access_policy=policy_store,
    )

    for user_type in ("customer", "staff", "guest", "admin", ""):
        user = UserContext(user_id="u-any", user_type=user_type)
        # general-chat에는 정책이 없으므로 모든 user_type 통과
        await svc.check_profile_access(user, "general-chat")


# ── 시나리오 4: Orchestrator가 segment 불허 Profile을 목록에서 제외 ──


@pytest.mark.asyncio
async def test_orchestrator_filters_by_segment():
    """MasterOrchestrator._get_available_profiles()가 segment 불허 Profile을 제외한다."""
    profiles = [
        StubProfile(id="hr-bot", name="HR Bot"),
        StubProfile(id="insurance-qa", name="Insurance QA"),
        StubProfile(id="general-chat", name="General Chat"),
    ]
    policy_store = _make_policy_store({
        "hr-bot": {"staff"},
        "insurance-qa": {"customer"},
        # general-chat: 정책 없음 -> 전체 공개
    })
    orch = _make_orchestrator(profiles, access_policy=policy_store)

    # customer 사용자: hr-bot 제외, insurance-qa/general-chat 포함
    user = UserContext(user_id="u1", user_type="customer")
    result = await orch._get_available_profiles(user)
    ids = [p["id"] for p in result]

    assert "hr-bot" not in ids, "customer는 staff 전용 hr-bot에 접근 불가"
    assert "insurance-qa" in ids, "customer는 customer 전용 insurance-qa에 접근 가능"
    assert "general-chat" in ids, "정책 없는 general-chat은 전체 공개"


# ── 시나리오 5: 복수 segment 허용 (customer + fa) -> 허용 ──


@pytest.mark.asyncio
async def test_multi_segment_profile():
    """복수 segment가 허용된 Profile에 각 segment 사용자가 접근할 수 있다."""
    policy_store = _make_policy_store({
        "insurance-qa": {"customer", "fa"},
    })
    svc = AuthService(
        pool=MagicMock(),
        auth_required=True,
        access_policy=policy_store,
    )

    # customer 접근 -> 허용
    user_customer = UserContext(user_id="c1", user_type="customer")
    await svc.check_profile_access(user_customer, "insurance-qa")

    # fa 접근 -> 허용
    user_fa = UserContext(user_id="f1", user_type="fa")
    await svc.check_profile_access(user_fa, "insurance-qa")

    # 미허용 segment -> 거부
    user_guest = UserContext(user_id="g1", user_type="guest")
    with pytest.raises(AuthError, match="사용자군 'guest'"):
        await svc.check_profile_access(user_guest, "insurance-qa")


# ── 시나리오 6: user_type="" -> 정책 있는 Profile 거부, 정책 없는 Profile 허용 ──


@pytest.mark.asyncio
async def test_empty_user_type_behavior():
    """user_type이 빈 문자열일 때: 정책 있는 Profile은 거부, 없는 Profile은 허용."""
    policy_store = _make_policy_store({
        "hr-bot": {"staff"},
        # general-chat: 정책 없음
    })
    svc = AuthService(
        pool=MagicMock(),
        auth_required=True,
        access_policy=policy_store,
    )
    user = UserContext(user_id="u-empty", user_type="")

    # 정책 있는 hr-bot -> 거부
    with pytest.raises(AuthError, match="사용자군 ''"):
        await svc.check_profile_access(user, "hr-bot")

    # 정책 없는 general-chat -> 허용
    await svc.check_profile_access(user, "general-chat")


# ── 시나리오 7: DB 정책 변경 후 reload() -> 새 정책 반영 ──


@pytest.mark.asyncio
async def test_policy_reload():
    """reload() 호출 후 변경된 정책이 즉시 반영된다."""
    # 초기 정책: hr-bot에 staff만 허용
    initial_rows = [
        {"profile_id": "hr-bot", "segment": "staff"},
    ]
    pool = AsyncMock()
    records_initial = []
    for row in initial_rows:
        record = MagicMock()
        record.__getitem__ = lambda self, key, r=row: r[key]
        records_initial.append(record)
    pool.fetch = AsyncMock(return_value=records_initial)

    store = AccessPolicyStore(pool)
    await store.load()

    # staff 허용, contractor 거부
    assert store.is_allowed("hr-bot", "staff") is True
    assert store.is_allowed("hr-bot", "contractor") is False

    # AuthService로 검증
    svc = AuthService(pool=MagicMock(), auth_required=True, access_policy=store)
    user_contractor = UserContext(user_id="u1", user_type="contractor")
    with pytest.raises(AuthError):
        await svc.check_profile_access(user_contractor, "hr-bot")

    # DB 정책 변경: contractor 추가
    updated_rows = [
        {"profile_id": "hr-bot", "segment": "staff"},
        {"profile_id": "hr-bot", "segment": "contractor"},
    ]
    records_updated = []
    for row in updated_rows:
        record = MagicMock()
        record.__getitem__ = lambda self, key, r=row: r[key]
        records_updated.append(record)
    pool.fetch = AsyncMock(return_value=records_updated)

    # reload -> 새 정책 반영
    await store.reload()

    # contractor 이제 허용
    assert store.is_allowed("hr-bot", "contractor") is True
    assert store.is_allowed("hr-bot", "staff") is True

    # AuthService로도 통과
    await svc.check_profile_access(user_contractor, "hr-bot")
