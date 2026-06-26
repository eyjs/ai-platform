"""LangGraph AsyncPostgresSaver 부트스트랩 헬퍼.

KNOWN GAP 결정 (G1, plan 12절) — ②안 채택:
  checkpoints/checkpoint_writes 테이블은 RLS 미적용 일반 테이블로 생성 (AsyncPostgresSaver.setup() 소유).
  thread_id는 외부에 노출하지 않고 어댑터(T4)만 접근한다.
  멀티테넌트 확장 시 thread_id 프리픽스 전략(예: "{tenant}:{session_id}")은 ADR(T6)에서 결정.

G5 (graceful 폴백):
  langgraph-checkpoint-postgres 또는 psycopg v3 미설치 환경에서 (None, None)을 반환한다.
  ImportError를 전파하지 않으므로 부트스트랩이 죽지 않는다.
"""

from src.observability.logging import get_logger

logger = get_logger(__name__)


def to_psycopg_conn_string(database_url: str) -> str:
    """asyncpg/sqlalchemy 드라이버 접두사를 psycopg v3 평문 URL로 정규화한다.

    psycopg v3는 postgresql:// 평문 URL을 그대로 받는다.
    운영 환경에서 postgresql+asyncpg:// 형태로 오는 경우를 대비해 제거한다.
    """
    return database_url.replace("+asyncpg", "")


async def build_checkpointer(
    database_url: str,
) -> "tuple[object, object] | tuple[None, None]":
    """AsyncPostgresSaver를 생성하고 setup(DDL 멱등)을 호출한다.

    Returns:
        (saver, context_manager) — 정상 초기화 시.
        (None, None) — 패키지 미설치 또는 연결 실패 시 (graceful 폴백, G5).

    사용 측(bootstrap.py)은 반환값이 (None, None)인 경우 체크포인터 없이 계속 진행한다.
    종료 시에는 context_manager.__aexit__(None, None, None)을 호출해 풀을 정리한다.
    """
    # lazy import: 패키지 미설치 시 ImportError를 여기서 잡아 graceful 반환.
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore[import]
    except ImportError as exc:
        logger.warning(
            "checkpointer_unavailable",
            reason="langgraph-checkpoint-postgres 또는 psycopg v3 미설치",
            error=str(exc),
        )
        return None, None

    conn_string = to_psycopg_conn_string(database_url)

    try:
        # AsyncPostgresSaver.from_conn_string()은 async context manager를 반환한다.
        # __aenter__()로 진입해 내부 psycopg 연결 풀을 열고 saver를 얻는다.
        cm = AsyncPostgresSaver.from_conn_string(conn_string)
        saver = await cm.__aenter__()
        # setup()은 checkpoints/checkpoint_writes 테이블을 멱등으로 생성한다.
        # (alembic이 아닌 PostgresSaver 소유 DDL — 이 외부에서 alembic 마이그레이션 생성 금지.)
        await saver.setup()
        logger.info("checkpointer_initialized", driver="psycopg_v3")
        return saver, cm
    except Exception as exc:
        logger.warning(
            "checkpointer_setup_failed",
            error=str(exc),
            conn=conn_string.split("@")[-1],  # 비밀번호 제외한 호스트/DB만 로깅
        )
        # context manager 진입 후 실패 시 __aexit__으로 정리 시도.
        try:
            await cm.__aexit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        return None, None
