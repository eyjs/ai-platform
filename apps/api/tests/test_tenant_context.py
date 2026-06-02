"""테넌트 RLS 컨텍스트 setup 훅 단위 테스트 (A2/4c)."""

from unittest.mock import AsyncMock

import pytest

from src.infrastructure.db.tenant_context import current_tenant, make_rls_setup


@pytest.mark.asyncio
async def test_setup_demotes_role_and_sets_guc_when_tenant():
    """테넌트 컨텍스트가 있으면 SET ROLE + GUC 설정."""
    token = current_tenant.set("tenant-A")
    try:
        conn = AsyncMock()
        setup = make_rls_setup("aip_app")
        await setup(conn)

        calls = [c.args for c in conn.execute.call_args_list]
        assert ("SET ROLE aip_app",) in calls
        # set_config는 (sql, tenant) 형태
        cfg = [c for c in calls if "set_config" in c[0]]
        assert cfg and cfg[0][1] == "tenant-A"
    finally:
        current_tenant.reset(token)


@pytest.mark.asyncio
async def test_setup_resets_when_no_tenant():
    """테넌트 컨텍스트가 없으면 RESET ROLE + GUC 초기화 (누설 방지)."""
    token = current_tenant.set(None)
    try:
        conn = AsyncMock()
        setup = make_rls_setup("aip_app")
        await setup(conn)

        calls = [c.args for c in conn.execute.call_args_list]
        assert ("RESET ROLE",) in calls
        # 리셋 경로는 빈 값을 SQL에 인라인 (파라미터 아님)
        cfg = [c for c in calls if "set_config" in c[0]]
        assert cfg and "''" in cfg[0][0]
    finally:
        current_tenant.reset(token)


def test_invalid_role_rejected():
    """SQL 조합 방어: 식별자 아닌 롤은 거부."""
    with pytest.raises(ValueError):
        make_rls_setup("aip_app; DROP TABLE documents")
    with pytest.raises(ValueError):
        make_rls_setup("123bad")
