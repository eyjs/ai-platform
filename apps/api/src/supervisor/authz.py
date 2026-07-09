"""Supervisor 위임 단일 관문 인가 재검사 (P0-4).

Supervisor 진입 시 호출자의 허용 프로파일 집합을 1회 확정하고(`resolve_allowed`),
위임 직전 매 서브 호출마다 `is_delegation_allowed`로 재검사하는 단일 관문을 제공한다.
deny-by-default 무결성(§0-3)을 이 모듈 하나로 강제한다 — 루프(supervisor loop)는
반드시 `is_delegation_allowed`만 호출하고 `allowed` 집합에 대한 `in` 검사를 직접
산재시키지 않는다.

기존 primitive(`src.domain.profile_authz`)를 그대로 재사용하며, 신규 인가 로직은
발명하지 않는다. `orchestrator.py::MasterOrchestrator._get_available_profiles`가
이미 수행하는 "API Key ∩ 테넌트 ∩ access_policy" 조합을 동일하게 재현하되, 프로파일
dict를 만들지 않고 "허용 id 집합"만 산출한다.

TODO(P2): orchestrator.py의 `_get_available_profiles`와 조합 로직이 중복된다.
두 경로의 공통 추출(dedup)은 P2에서 orchestrator를 함께 손볼 때 처리한다. P0에서는
orchestrator.py를 수정하지 않는다(직접/오케스트레이터 경로 회귀 위험 회피).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from src.domain.profile_authz import is_profile_allowed, resolve_allowed_profiles
from src.observability.logging import get_logger

if TYPE_CHECKING:
    from src.agent.profile_store import ProfileStore
    from src.config import Settings
    from src.gateway.access_policy import AccessPolicyStore
    from src.services.tenant_service import TenantService

logger = get_logger(__name__)


class _UserCtx(Protocol):
    """resolve_allowed가 요구하는 최소 사용자 컨텍스트 형태(구조적 타입)."""

    allowed_profiles: list[str] | None


class DelegationAuthorizer:
    """Supervisor 위임 인가의 단일 관문.

    `resolve_allowed`로 호출자의 허용 프로파일 집합을 1회 산출하고,
    `is_delegation_allowed`로 서브 위임 매 호출마다 재검사한다.
    """

    def __init__(
        self,
        profile_store: "ProfileStore",
        tenant_service: "TenantService",
        access_policy: "AccessPolicyStore | None",
        settings: "Settings",
    ) -> None:
        self._profile_store = profile_store
        self._tenant = tenant_service
        self._access_policy = access_policy
        self._settings = settings

    async def resolve_allowed(self, user_ctx: _UserCtx) -> set[str] | None:
        """API Key ∩ 테넌트 ∩ access_policy를 조합해 허용 프로파일 집합을 산출한다.

        Returns:
            - None     → 전체 허용(필터 없음).
            - set()    → 전체 거부(deny-by-default).
            - set(ids) → 명시된 프로필만 허용.
        """
        strict = self._settings.profile_auth_strict

        # API Key의 allowed_profiles 필터 (A1: strict면 빈 목록=전체 거부)
        api_allowed = resolve_allowed_profiles(user_ctx.allowed_profiles, strict=strict)

        # 테넌트 필터: tenant_id가 있으면 매핑 적용. strict면 빈 매핑=전체 거부.
        # tenant_id가 아예 없는 키는 테넌트 필터를 적용하지 않는다(키 단위 인가가 관장).
        # (§8 R6: 오케스트레이터 정책과 격차를 만들지 않는다 — 변경 금지, 경고 로그만.)
        tenant_id = getattr(user_ctx, "tenant_id", None)
        tenant_allowed: set[str] | None = None
        if tenant_id:
            tenant_profile_ids = await self._tenant.get_allowed_profiles(tenant_id)
            tenant_allowed = resolve_allowed_profiles(tenant_profile_ids, strict=strict)
        elif strict:
            logger.warning(
                "orchestrator_profile_auth_no_tenant",
                user_id=getattr(user_ctx, "user_id", ""),
            )

        # segment 필터를 위한 user_type 추출
        user_type = getattr(user_ctx, "user_type", "")

        # api/tenant 둘 다 전체 허용(None)이고 access_policy도 없으면 필터 없음(None) 반환
        if api_allowed is None and tenant_allowed is None and self._access_policy is None:
            return None

        all_profiles = await self._profile_store.list_all()

        allowed_ids: set[str] = set()
        for p in all_profiles:
            if not is_profile_allowed(api_allowed, p.id):
                continue
            if not is_profile_allowed(tenant_allowed, p.id):
                continue
            if self._access_policy and not self._access_policy.is_allowed(p.id, user_type):
                continue
            allowed_ids.add(p.id)

        return allowed_ids

    def is_delegation_allowed(self, allowed: set[str] | None, profile_id: str) -> bool:
        """위임 직전 매 서브 호출마다 재검사하는 단일 관문.

        루프(supervisor loop)는 이 메서드만 호출한다(직접 `in` 검사 산재 금지).
        """
        return is_profile_allowed(allowed, profile_id)
