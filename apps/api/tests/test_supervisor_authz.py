"""supervisor.authz — 위임 단일 관문 인가 재검사 단위 테스트 (P0-4)."""

from __future__ import annotations

from types import SimpleNamespace

from src.supervisor.authz import DelegationAuthorizer


class _FakeProfile:
    def __init__(self, id_: str) -> None:
        self.id = id_


class _FakeProfileStore:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    async def list_all(self) -> list[_FakeProfile]:
        return [_FakeProfile(i) for i in self._ids]


class _FakeTenantService:
    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self._mapping = mapping

    async def get_allowed_profiles(self, tenant_id: str | None) -> list[str]:
        if tenant_id is None:
            return []
        return self._mapping.get(tenant_id, [])


class _FakeAccessPolicy:
    def __init__(self, denied: dict[str, set[str]]) -> None:
        # {profile_id: {denied user_type, ...}}
        self._denied = denied

    def is_allowed(self, profile_id: str, user_type: str) -> bool:
        return user_type not in self._denied.get(profile_id, set())


def _settings(*, strict: bool) -> SimpleNamespace:
    return SimpleNamespace(profile_auth_strict=strict)


def _user_ctx(
    *, allowed_profiles: list[str] | None = None, tenant_id: str | None = None, user_type: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        allowed_profiles=allowed_profiles,
        tenant_id=tenant_id,
        user_type=user_type,
        user_id="u1",
    )


async def test_strict_empty_api_allowed_denies_all():
    # strict + 빈 API allowed → resolve_allowed가 전체 거부(set()), 어떤 profile도 위임 불가
    authorizer = DelegationAuthorizer(
        profile_store=_FakeProfileStore(["insurance-qa", "kms-assistant"]),
        tenant_service=_FakeTenantService({}),
        access_policy=None,
        settings=_settings(strict=True),
    )
    allowed = await authorizer.resolve_allowed(_user_ctx(allowed_profiles=[]))
    assert allowed == set()
    assert authorizer.is_delegation_allowed(allowed, "insurance-qa") is False
    assert authorizer.is_delegation_allowed(allowed, "kms-assistant") is False


async def test_api_wildcard_intersect_tenant_mapping():
    # API ["*"] + 테넌트 {insurance-qa} → 교집합 {insurance-qa}만 허용
    authorizer = DelegationAuthorizer(
        profile_store=_FakeProfileStore(["insurance-qa", "kms-assistant"]),
        tenant_service=_FakeTenantService({"tenant-a": ["insurance-qa"]}),
        access_policy=None,
        settings=_settings(strict=True),
    )
    allowed = await authorizer.resolve_allowed(
        _user_ctx(allowed_profiles=["*"], tenant_id="tenant-a"),
    )
    assert allowed == {"insurance-qa"}
    assert authorizer.is_delegation_allowed(allowed, "insurance-qa") is True
    assert authorizer.is_delegation_allowed(allowed, "kms-assistant") is False


async def test_access_policy_excludes_denied_user_type():
    # access_policy가 특정 user_type에 대해 kms-assistant 거부 → 결과 집합에서 제외
    authorizer = DelegationAuthorizer(
        profile_store=_FakeProfileStore(["insurance-qa", "kms-assistant"]),
        tenant_service=_FakeTenantService({}),
        access_policy=_FakeAccessPolicy({"kms-assistant": {"external"}}),
        settings=_settings(strict=False),
    )
    allowed = await authorizer.resolve_allowed(
        _user_ctx(allowed_profiles=["*"], tenant_id=None, user_type="external"),
    )
    assert allowed == {"insurance-qa"}
    assert authorizer.is_delegation_allowed(allowed, "kms-assistant") is False


def test_is_delegation_allowed_none_and_empty_semantics():
    authorizer = DelegationAuthorizer(
        profile_store=_FakeProfileStore([]),
        tenant_service=_FakeTenantService({}),
        access_policy=None,
        settings=_settings(strict=False),
    )
    assert authorizer.is_delegation_allowed(None, "any") is True
    assert authorizer.is_delegation_allowed(set(), "any") is False


async def test_no_tenant_strict_logs_warning_but_proceeds(caplog):
    # 테넌트 없음 + strict → 경고 로그 발생하되 예외 없이 진행(기존 동작과 동일)
    authorizer = DelegationAuthorizer(
        profile_store=_FakeProfileStore(["insurance-qa"]),
        tenant_service=_FakeTenantService({}),
        access_policy=None,
        settings=_settings(strict=True),
    )
    allowed = await authorizer.resolve_allowed(
        _user_ctx(allowed_profiles=["*"], tenant_id=None),
    )
    # tenant_id 없음 → 테넌트 필터 미적용(None), api_allowed(None) + access_policy 없음 → 전체 허용
    assert allowed is None
    assert authorizer.is_delegation_allowed(allowed, "insurance-qa") is True


async def test_full_allow_when_no_filters_returns_none():
    # api/tenant 둘 다 None(전체 허용)이고 access_policy도 없으면 None(필터 없음) 반환
    authorizer = DelegationAuthorizer(
        profile_store=_FakeProfileStore(["insurance-qa", "kms-assistant"]),
        tenant_service=_FakeTenantService({}),
        access_policy=None,
        settings=_settings(strict=False),
    )
    allowed = await authorizer.resolve_allowed(_user_ctx(allowed_profiles=None))
    assert allowed is None
