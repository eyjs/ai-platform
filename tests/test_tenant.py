"""TenantService 단위 테스트."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.orchestrator.tenant import TenantService


@pytest.fixture
def mock_pool():
    pool = AsyncMock()
    pool.acquire = MagicMock()
    return pool


@pytest.fixture
def tenant_service(mock_pool):
    return TenantService(mock_pool)


@pytest.mark.asyncio
async def test_get_allowed_profiles_none_tenant(tenant_service):
    """tenant_id가 None이면 빈 리스트를 반환한다."""
    result = await tenant_service.get_allowed_profiles(None)
    assert result == []


@pytest.mark.asyncio
async def test_get_allowed_profiles(tenant_service, mock_pool):
    """테넌트에 할당된 프로필 목록을 반환한다."""
    mock_pool.fetch.return_value = [
        {"profile_id": "insurance-qa"},
        {"profile_id": "fee-calc"},
    ]

    result = await tenant_service.get_allowed_profiles("tenant-a")

    assert result == ["insurance-qa", "fee-calc"]
    mock_pool.fetch.assert_called_once()


@pytest.mark.asyncio
async def test_get_tenant(tenant_service, mock_pool):
    """테넌트를 조회한다."""
    mock_pool.fetchrow.return_value = {
        "id": "tenant-a",
        "name": "테넌트 A",
        "orchestrator_enabled": True,
        "default_chatbot_id": "insurance-qa",
        "is_active": True,
    }

    result = await tenant_service.get("tenant-a")

    assert result is not None
    assert result.id == "tenant-a"
    assert result.name == "테넌트 A"
    assert result.orchestrator_enabled is True


@pytest.mark.asyncio
async def test_get_tenant_not_found(tenant_service, mock_pool):
    """존재하지 않는 테넌트 조회 시 None을 반환한다."""
    mock_pool.fetchrow.return_value = None

    result = await tenant_service.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_create_tenant(tenant_service, mock_pool):
    """테넌트를 생성한다."""
    mock_pool.execute.return_value = "INSERT 0 1"

    result = await tenant_service.create(
        tenant_id="tenant-b",
        name="테넌트 B",
        description="테스트 테넌트",
    )

    assert result.id == "tenant-b"
    assert result.name == "테넌트 B"
    mock_pool.execute.assert_called_once()


@pytest.mark.asyncio
async def test_deactivate_tenant(tenant_service, mock_pool):
    """테넌트를 비활성화한다."""
    mock_pool.execute.return_value = "UPDATE 1"

    result = await tenant_service.deactivate("tenant-a")
    assert result is True


@pytest.mark.asyncio
async def test_deactivate_nonexistent(tenant_service, mock_pool):
    """존재하지 않는 테넌트 비활성화 시 False를 반환한다."""
    mock_pool.execute.return_value = "UPDATE 0"

    result = await tenant_service.deactivate("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_set_profiles(tenant_service, mock_pool):
    """프로필 전체 교체."""
    mock_conn = AsyncMock()
    # transaction()은 일반 메서드로 async context manager 반환
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    # acquire()도 async context manager
    mock_acq = MagicMock()
    mock_acq.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acq.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=mock_acq)

    await tenant_service.set_profiles("tenant-a", ["insurance-qa", "fee-calc"])

    # DELETE + 2 INSERT
    assert mock_conn.execute.call_count == 3


@pytest.mark.asyncio
async def test_add_profile(tenant_service, mock_pool):
    """프로필 추가."""
    mock_pool.execute.return_value = "INSERT 0 1"

    await tenant_service.add_profile("tenant-a", "new-profile")
    mock_pool.execute.assert_called_once()


@pytest.mark.asyncio
async def test_remove_profile(tenant_service, mock_pool):
    """프로필 제거."""
    mock_pool.execute.return_value = "DELETE 1"

    result = await tenant_service.remove_profile("tenant-a", "insurance-qa")
    assert result is True
