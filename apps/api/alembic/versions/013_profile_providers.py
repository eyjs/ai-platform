"""agent_profiles.config.providers 블록 지원 (GIN 인덱스) + profile_provider_stats 뷰

Revision ID: 013
Create Date: 2026-04-20

- providers 블록 쿼리 성능을 위한 GIN index
- 대시보드용 뷰: profile_provider_stats (request_logs 집계)
"""

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # providers 블록 GIN 인덱스 (agent_profiles.config JSONB)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_profiles_config_providers
            ON agent_profiles USING GIN ((config->'providers'));
    """)

    # 대시보드용 뷰 — 지난 24시간 집계
    op.execute("""
        CREATE OR REPLACE VIEW profile_provider_stats AS
        SELECT
            profile_id,
            provider_id,
            COUNT(*) AS request_count,
            AVG(CASE WHEN status_code >= 400 THEN 1.0 ELSE 0.0 END) AS error_rate_24h,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
        FROM api_request_logs
        WHERE ts > NOW() - INTERVAL '24 hours'
          AND profile_id IS NOT NULL
        GROUP BY profile_id, provider_id;
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS profile_provider_stats;")
    op.execute("DROP INDEX IF EXISTS idx_agent_profiles_config_providers;")
