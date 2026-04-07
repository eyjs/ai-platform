"""initial schema

Revision ID: 001
Create Date: 2026-03-12
"""

revision = "001"
down_revision = None

from alembic import op


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 에이전트 프로필
    op.execute("""
        CREATE TABLE agent_profiles (
            id VARCHAR(100) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            config JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # 문서 (자체 관리, KMS FK 없음)
    op.execute("""
        CREATE TABLE documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            external_id VARCHAR(255),
            title VARCHAR(500) NOT NULL,
            file_name VARCHAR(500),
            file_hash VARCHAR(64),
            domain_code VARCHAR(50) NOT NULL,
            security_level VARCHAR(20) DEFAULT 'PUBLIC',
            source_url TEXT,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(file_hash, domain_code)
        );
    """)

    # 문서 청크 (벡터 + FTS + 트라이그램)
    op.execute("""
        CREATE TABLE document_chunks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
            chunk_index INT NOT NULL,
            content TEXT NOT NULL,
            token_count INT,
            embedding vector(1024),
            search_vector tsvector,
            domain_code VARCHAR(50) NOT NULL,
            security_level VARCHAR(20) DEFAULT 'PUBLIC',
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # 구조화된 팩트 (chain resolution)
    op.execute("""
        CREATE TABLE facts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
            domain_code VARCHAR(50) NOT NULL,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            heading_path TEXT[] DEFAULT '{}',
            table_context TEXT DEFAULT '',
            confidence FLOAT DEFAULT 1.0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # 대화 세션 (Redis 대체 - 일반 테이블, 영속)
    op.execute("""
        CREATE TABLE conversation_sessions (
            id VARCHAR(255) PRIMARY KEY,
            profile_id VARCHAR(100) REFERENCES agent_profiles(id),
            user_id VARCHAR(255),
            turns JSONB DEFAULT '[]',
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ
        );
    """)

    # 캐시 테이블 (Redis 대체 - UNLOGGED, WAL 없음)
    op.execute("""
        CREATE UNLOGGED TABLE cache_entries (
            key VARCHAR(500) PRIMARY KEY,
            value JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL
        );
    """)

    # 작업 큐 (Redis/BullMQ 대체 - SKIP LOCKED 패턴)
    op.execute("""
        CREATE TABLE job_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            queue_name VARCHAR(100) NOT NULL,
            payload JSONB NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            priority INT DEFAULT 0,
            attempts INT DEFAULT 0,
            max_attempts INT DEFAULT 3,
            last_error TEXT,
            locked_by VARCHAR(255),
            locked_at TIMESTAMPTZ,
            scheduled_at TIMESTAMPTZ DEFAULT NOW(),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        );
    """)

    # 워크플로우 상태 (Redis 대체 - 일반 테이블, 영속)
    op.execute("""
        CREATE TABLE workflow_states (
            id VARCHAR(255) PRIMARY KEY,
            session_id VARCHAR(255) REFERENCES conversation_sessions(id) ON DELETE CASCADE,
            workflow_id VARCHAR(100) NOT NULL,
            current_step VARCHAR(100) NOT NULL,
            state JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # --- 인덱스 ---

    # 벡터 검색 (HNSW)
    op.execute("""
        CREATE INDEX idx_chunks_embedding_hnsw
            ON document_chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 200);
    """)

    # Full-text 검색 (GIN)
    op.execute("""
        CREATE INDEX idx_chunks_search_vector
            ON document_chunks USING gin(search_vector);
    """)

    # Trigram 유사도 (GIN)
    op.execute("""
        CREATE INDEX idx_chunks_content_trgm
            ON document_chunks USING gin(content gin_trgm_ops);
    """)

    # 도메인 + 보안등급 필터
    op.execute("""
        CREATE INDEX idx_chunks_domain_security
            ON document_chunks (domain_code, security_level);
    """)

    op.execute("""
        CREATE INDEX idx_documents_domain
            ON documents (domain_code);
    """)

    # 팩트 검색
    op.execute("""
        CREATE INDEX idx_facts_domain
            ON facts (domain_code);
    """)

    op.execute("""
        CREATE INDEX idx_facts_subject_trgm
            ON facts USING gin(subject gin_trgm_ops);
    """)

    # 작업 큐 (SKIP LOCKED 패턴)
    op.execute("""
        CREATE INDEX idx_job_queue_pending
            ON job_queue (queue_name, priority DESC, scheduled_at)
            WHERE status = 'pending';
    """)

    # 캐시 만료 정리용
    op.execute("""
        CREATE INDEX idx_cache_expires
            ON cache_entries (expires_at);
    """)

    # 세션 만료 정리용
    op.execute("""
        CREATE INDEX idx_sessions_expires
            ON conversation_sessions (expires_at)
            WHERE expires_at IS NOT NULL;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflow_states CASCADE;")
    op.execute("DROP TABLE IF EXISTS job_queue CASCADE;")
    op.execute("DROP TABLE IF EXISTS cache_entries CASCADE;")
    op.execute("DROP TABLE IF EXISTS conversation_sessions CASCADE;")
    op.execute("DROP TABLE IF EXISTS facts CASCADE;")
    op.execute("DROP TABLE IF EXISTS document_chunks CASCADE;")
    op.execute("DROP TABLE IF EXISTS documents CASCADE;")
    op.execute("DROP TABLE IF EXISTS agent_profiles CASCADE;")
