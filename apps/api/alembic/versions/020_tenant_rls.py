"""테넌트 Row Level Security — DB 테넌트 격리 4c (A2)

Revision ID: 020
Create Date: 2026-06-02

애플리케이션 필터(4b)를 한 줄 실수에서 DB로 이전하는 심층 방어.
- 비특권 런타임 롤 aip_app 생성(NOSUPERUSER NOBYPASSRLS). 런타임은 요청마다
  SET ROLE aip_app 로 강등해 RLS 적용을 받는다(superuser는 RLS를 우회하므로).
- 6개 테넌트 테이블에 ROW LEVEL SECURITY + tenant_isolation 정책.
  current_setting('app.current_tenant')가 비면(백그라운드/superuser 경로) 전체 허용,
  설정되면 해당 테넌트 행만. WITH CHECK로 타 테넌트 쓰기도 차단.

정책만으로는 동작 무변(superuser aip 접속 시 우회). AIP_RLS_ENABLED=true +
SET ROLE 강등 시에만 실효. 완전 가역.
"""

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

from alembic import op

_TABLES = (
    "documents",
    "document_chunks",
    "facts",
    "conversation_sessions",
    "workflow_states",
    "response_cache",
)

# current_tenant GUC가 비면(NULL) 전체 허용(백그라운드/superuser), 설정되면 테넌트 일치만
_PREDICATE = (
    "current_setting('app.current_tenant', true) IS NULL "
    "OR current_setting('app.current_tenant', true) = '' "
    "OR tenant_id = current_setting('app.current_tenant', true)"
)


def upgrade() -> None:
    # 1) 비특권 런타임 롤 (RLS 적용 대상). SET ROLE 로 강등해 사용 — LOGIN 불필요.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aip_app') THEN
                CREATE ROLE aip_app NOSUPERUSER NOBYPASSRLS NOLOGIN;
            END IF;
        END $$;
    """)
    # aip_app은 RLS만 받을 뿐 기능은 동일해야 하므로 전체 권한 부여 (격리는 RLS가 담당)
    op.execute("GRANT USAGE ON SCHEMA public TO aip_app;")
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO aip_app;")
    op.execute("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO aip_app;")
    op.execute("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO aip_app;")
    # 향후 생성 객체 기본 권한
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT ALL ON TABLES TO aip_app;
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT ALL ON SEQUENCES TO aip_app;
    """)

    # 2) RLS 정책
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING ({_PREDICATE})
                WITH CHECK ({_PREDICATE});
        """)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM aip_app;
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM aip_app;
    """)
    op.execute("REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM aip_app;")
    op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM aip_app;")
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM aip_app;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM aip_app;")
    op.execute("DROP ROLE IF EXISTS aip_app;")
