"""Segment 교차 검증 통합 테스트.

AuthService.check_profile_access()와 MasterOrchestrator._get_available_profiles()에서
AccessPolicyStore 기반 segment 필터링이 올바르게 동작하는지 검증한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.gateway.auth import AuthError, AuthService
from src.gateway.models import UserContext


# ── 헬퍼: 가벼운 AccessPolicyStore 스텁 ──


class FakeAccessPolicyStore:
    """테스트용 AccessPolicyStore 스텁."""

    def __init__(self, policies: dict[str, set[str]] | None = None):
        self._policies = policies or {}

    def is_allowed(self, profile_id: str, user_type: str) -> bool:
        allowed = self._policies.get(profile_id)
        if allowed is None:
            return True
        if not user_type:
            return False
        return user_type in allowed

    def get_allowed_segments(self, profile_id: str) -> set[str]:
        return set(self._policies.get(profile_id, set()))


# ── AuthService segment 검증 테스트 ──


@pytest.fixture
def pool_mock():
    return MagicMock()


class TestCheckProfileAccessSegment:
    """AuthService.check_profile_access segment 교차 검증."""

    @pytest.mark.asyncio
    async def test_check_profile_access_segment_allowed(self, pool_mock):
        """허용된 segment의 사용자는 프로필에 접근할 수 있다."""
        policy = FakeAccessPolicyStore({"hr-bot": {"employee", "manager"}})
        svc = AuthService(pool=pool_mock, auth_required=True, access_policy=policy)

        user = UserContext(user_id="u1", user_type="employee")
        # 예외 없이 통과해야 한다
        await svc.check_profile_access(user, "hr-bot")

    @pytest.mark.asyncio
    async def test_check_profile_access_segment_denied(self, pool_mock):
        """허용되지 않은 segment의 사용자는 AuthError가 발생한다."""
        policy = FakeAccessPolicyStore({"hr-bot": {"employee", "manager"}})
        svc = AuthService(pool=pool_mock, auth_required=True, access_policy=policy)

        user = UserContext(user_id="u2", user_type="guest")
        with pytest.raises(AuthError, match="사용자군 'guest'"):
            await svc.check_profile_access(user, "hr-bot")

    @pytest.mark.asyncio
    async def test_check_profile_access_no_policy_allows_all(self, pool_mock):
        """정책이 없는 프로필은 모든 user_type이 접근 가능하다."""
        policy = FakeAccessPolicyStore({"hr-bot": {"employee"}})
        svc = AuthService(pool=pool_mock, auth_required=True, access_policy=policy)

        user = UserContext(user_id="u3", user_type="anyone")
        # "general-chat"에 대한 정책이 없으므로 통과
        await svc.check_profile_access(user, "general-chat")

    @pytest.mark.asyncio
    async def test_check_profile_access_no_store_skips(self, pool_mock):
        """AccessPolicyStore가 주입되지 않으면 segment 검증을 건너뛴다."""
        svc = AuthService(pool=pool_mock, auth_required=True, access_policy=None)

        user = UserContext(user_id="u4", user_type="guest")
        # access_policy가 None이므로 segment 검증 스킵 -> 통과
        await svc.check_profile_access(user, "hr-bot")


# ── MasterOrchestrator segment 필터 테스트 ──


@dataclass
class FakeProfile:
    """테스트용 프로필 스텁."""

    id: str
    name: str = ""
    description: str = ""
    domain_scopes: list[str] = field(default_factory=list)
    intent_hints: list = field(default_factory=list)


class TestGetAvailableProfilesSegment:
    """MasterOrchestrator._get_available_profiles segment 필터링."""

    def _make_orchestrator(self, profiles, access_policy=None):
        """테스트용 MasterOrchestrator를 생성한다."""
        from src.orchestrator.orchestrator import MasterOrchestrator

        profile_store = AsyncMock()
        profile_store.list_all = AsyncMock(return_value=profiles)

        tenant_service = AsyncMock()
        tenant_service.get_allowed_profiles = AsyncMock(return_value=[])

        orch = MasterOrchestrator(
            llm=MagicMock(),
            profile_store=profile_store,
            session_memory=MagicMock(),
            workflow_engine=MagicMock(),
            tenant_service=tenant_service,
            access_policy=access_policy,
        )
        return orch

    @pytest.mark.asyncio
    async def test_get_available_profiles_filters_by_segment(self):
        """segment 정책이 있으면 허용되지 않은 프로필은 필터링된다."""
        profiles = [
            FakeProfile(id="hr-bot", name="HR Bot"),
            FakeProfile(id="general-chat", name="General Chat"),
        ]
        policy = FakeAccessPolicyStore({"hr-bot": {"employee"}})
        orch = self._make_orchestrator(profiles, access_policy=policy)

        user = UserContext(user_id="u1", user_type="guest")
        result = await orch._get_available_profiles(user)

        ids = [p["id"] for p in result]
        assert "hr-bot" not in ids, "guest는 hr-bot에 접근 불가"
        assert "general-chat" in ids, "정책 없는 프로필은 포함"

    @pytest.mark.asyncio
    async def test_get_available_profiles_no_policy_includes_all(self):
        """AccessPolicyStore가 없으면 모든 프로필이 포함된다."""
        profiles = [
            FakeProfile(id="hr-bot", name="HR Bot"),
            FakeProfile(id="general-chat", name="General Chat"),
        ]
        orch = self._make_orchestrator(profiles, access_policy=None)

        user = UserContext(user_id="u1", user_type="guest")
        result = await orch._get_available_profiles(user)

        ids = [p["id"] for p in result]
        assert "hr-bot" in ids
        assert "general-chat" in ids
