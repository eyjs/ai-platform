"""테넌트 RLS 컨텍스트 — 요청 단위 tenant_id를 DB 커넥션에 주입 (A2/4c).

요청 핸들러가 current_tenant 컨텍스트변수를 설정하면, asyncpg 풀의 setup 콜백이
매 acquire마다 그 값을 읽어 커넥션을 비특권 롤(aip_app)로 강등하고
app.current_tenant GUC를 설정한다 → RLS 정책이 테넌트 행만 보이게 강제.

컨텍스트변수가 비어 있으면(백그라운드/워커/인증 전 단계) RESET ROLE 로 superuser를
유지해 RLS를 우회한다(전체 접근). 이로써 신뢰된 서버 경로는 깨지지 않는다.

setup은 매 acquire마다 실행되므로, 이전 요청이 남긴 SET ROLE/GUC가 다음 사용자에게
누설되지 않는다(매번 설정 또는 RESET).
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Awaitable, Callable, Optional

import asyncpg

# 요청 단위 테넌트. 게이트웨이가 인증 후 설정한다.
current_tenant: ContextVar[Optional[str]] = ContextVar("current_tenant", default=None)

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def make_rls_setup(role: str = "aip_app") -> Callable[[asyncpg.Connection], Awaitable[None]]:
    """asyncpg 풀의 setup 콜백을 만든다. 매 acquire마다 실행된다.

    role은 식별자로 검증한다(상수지만 SQL 조합 방어).
    """
    if not _IDENTIFIER.match(role):
        raise ValueError(f"유효하지 않은 롤 식별자: {role}")

    async def _setup(conn: asyncpg.Connection) -> None:
        tenant = current_tenant.get()
        if tenant:
            # 비특권 롤로 강등 → RLS 적용. GUC로 테넌트 지정.
            await conn.execute(f"SET ROLE {role}")
            await conn.execute("SELECT set_config('app.current_tenant', $1, false)", tenant)
        else:
            # 테넌트 컨텍스트 없음(백그라운드/워커) → superuser 유지, GUC 초기화.
            await conn.execute("RESET ROLE")
            await conn.execute("SELECT set_config('app.current_tenant', '', false)")

    return _setup
