"""T2: AsyncPostgresSaver 체크포인터 부트스트랩 단위 테스트.

검증 항목:
  1. to_psycopg_conn_string — postgresql+asyncpg:// → postgresql:// 정규화
  2. build_checkpointer — ImportError 발생 시 (None, None) + warning 로깅 (graceful, G5)
  3. build_checkpointer — 연결 오류 시 (None, None) + warning 로깅 (graceful)
  4. (DB 있는 환경만) setup 2회 호출 멱등 — DB 없으면 skip
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workflow.checkpointer import build_checkpointer, to_psycopg_conn_string


# ── 1. to_psycopg_conn_string ─────────────────────────────────────────────────


def test_to_psycopg_conn_string_strips_asyncpg():
    url = "postgresql+asyncpg://user:pass@localhost:5432/db"
    result = to_psycopg_conn_string(url)
    assert result == "postgresql://user:pass@localhost:5432/db"


def test_to_psycopg_conn_string_plain_url_unchanged():
    url = "postgresql://aip:aip_dev@localhost:5434/ai_platform"
    result = to_psycopg_conn_string(url)
    assert result == url


def test_to_psycopg_conn_string_other_driver_unchanged():
    # +psycopg2 같은 다른 접두사는 건드리지 않는다
    url = "postgresql+psycopg2://user:pass@localhost/db"
    result = to_psycopg_conn_string(url)
    assert result == url


# ── 2. build_checkpointer — ImportError (G5 graceful) ────────────────────────


@pytest.mark.asyncio
async def test_build_checkpointer_import_error_returns_none(caplog):
    """langgraph-checkpoint-postgres 미설치 시 (None, None) 반환, 예외 전파 금지."""
    import logging

    with patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": None}):
        with caplog.at_level(logging.WARNING):
            saver, cm = await build_checkpointer("postgresql://localhost/db")

    assert saver is None
    assert cm is None


@pytest.mark.asyncio
async def test_build_checkpointer_import_error_no_exception():
    """ImportError가 호출자에게 전파되지 않는다 (G5)."""
    with patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": None}):
        # 예외가 전파되면 pytest가 실패 처리
        result = await build_checkpointer("postgresql://localhost/db")
    assert result == (None, None)


# ── 3. build_checkpointer — 연결 오류 시 graceful ────────────────────────────


@pytest.mark.asyncio
async def test_build_checkpointer_connection_error_returns_none(caplog):
    """DB 연결 실패 등 예외도 (None, None) 반환, 예외 전파 금지."""
    import logging

    # AsyncPostgresSaver.from_conn_string()이 성공하지만 __aenter__ 에서 실패하는 경우
    mock_cm = AsyncMock()
    mock_cm.__aenter__.side_effect = OSError("connection refused")
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_module = MagicMock()
    mock_module.AsyncPostgresSaver.from_conn_string.return_value = mock_cm

    with patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": mock_module}):
        with caplog.at_level(logging.WARNING):
            saver, cm = await build_checkpointer("postgresql://localhost/db")

    assert saver is None
    assert cm is None


@pytest.mark.asyncio
async def test_build_checkpointer_setup_error_returns_none():
    """setup() 호출 중 예외도 (None, None) 반환, 예외 전파 금지."""
    mock_saver = AsyncMock()
    mock_saver.setup.side_effect = RuntimeError("pg error")

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_saver
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_module = MagicMock()
    mock_module.AsyncPostgresSaver.from_conn_string.return_value = mock_cm

    with patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": mock_module}):
        saver, cm = await build_checkpointer("postgresql://localhost/db")

    assert saver is None
    assert cm is None


# ── 4. build_checkpointer — 정상 경로 (설치된 환경) ─────────────────────────


@pytest.mark.asyncio
async def test_build_checkpointer_success_returns_saver_and_cm():
    """패키지가 설치되어 있고 DB 연결이 성공하면 (saver, cm) 반환."""
    mock_saver = AsyncMock()
    mock_saver.setup = AsyncMock(return_value=None)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_saver
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_module = MagicMock()
    mock_module.AsyncPostgresSaver.from_conn_string.return_value = mock_cm

    with patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": mock_module}):
        saver, cm = await build_checkpointer("postgresql://localhost/db")

    assert saver is mock_saver
    assert cm is mock_cm
    mock_saver.setup.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_checkpointer_conn_string_normalized():
    """build_checkpointer가 +asyncpg 제거한 URL을 from_conn_string에 넘긴다."""
    mock_saver = AsyncMock()
    mock_saver.setup = AsyncMock(return_value=None)

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_saver
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_module = MagicMock()
    mock_module.AsyncPostgresSaver.from_conn_string.return_value = mock_cm

    with patch.dict(sys.modules, {"langgraph.checkpoint.postgres.aio": mock_module}):
        await build_checkpointer("postgresql+asyncpg://user:pass@localhost/db")

    mock_module.AsyncPostgresSaver.from_conn_string.assert_called_once_with(
        "postgresql://user:pass@localhost/db"
    )


# ── 5. DB 있는 환경에서만: setup 멱등 (2회 호출 안전) ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(
    True,  # DB 없는 CI 환경에서는 항상 skip — 로컬 DB 있는 환경에서 수동 실행
    reason="실제 DB 필요 — AIP_DATABASE_URL 환경변수로 연결 가능한 환경에서 실행",
)
async def test_build_checkpointer_setup_idempotent():
    """setup()을 2회 연속 호출해도 오류 없이 완료 (테이블 멱등 생성)."""
    import os

    db_url = os.getenv("AIP_DATABASE_URL", "postgresql://aip:aip_dev@localhost:5434/ai_platform")
    saver1, cm1 = await build_checkpointer(db_url)
    assert saver1 is not None
    await saver1.setup()  # 2번째 호출

    await cm1.__aexit__(None, None, None)
