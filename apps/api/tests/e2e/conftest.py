"""E2E 라이브 하니스 공통 fixture (P3 Step 17a).

게이팅 원칙 (절대):
- AIP_E2E_LIVE != "1"  → 모듈 전체 skip + 명시적 사유.
- 서비스 헬스 프리체크 실패 → skip + 어떤 서비스가 다운인지 사유.
- 라이브/DB 미가용을 **조용한 통과로 처리하지 않는다.** 항상 skip + reason.

라이브 의존:
- KMS(:3001), ai-platform(:8020), docforge(:5051) 컨테이너
- 호스트 OCR(:5052), 임베딩(:8103)  (실패주입③ 대상)
- ai-platform PostgreSQL (DB 단언용)

이 conftest 는 ai-platform repo 안에 있지만, **프로덕션 코드를 import 하지 않는다.**
seam 검증은 전부 HTTP / DB 경유 (앱간 경계 규칙 준수).
"""

from __future__ import annotations

import os
import time

import pytest

# ---------------------------------------------------------------------------
# 라이브 게이트 & 엔드포인트 설정 (env override 가능)
# ---------------------------------------------------------------------------

#: AIP_E2E_LIVE=1 일 때만 라이브 E2E 활성화. 그 외에는 전체 skip.
LIVE_ENV_KEY = "AIP_E2E_LIVE"

#: 헬스 프리체크 대상. (이름, base_url, health path)
DEFAULT_SERVICES = (
    ("kms", os.environ.get("AIP_E2E_KMS_URL", "http://localhost:3001"), "/api/health"),
    ("ai-platform", os.environ.get("AIP_E2E_AIP_URL", "http://localhost:8020"), "/api/health"),
    ("docforge", os.environ.get("AIP_E2E_DOCFORGE_URL", "http://localhost:5051"), "/v1/health"),
)

#: KMS JWT 발급용 dev 시크릿 (핸드오프 §6, KMS dev 환경 기본값).
KMS_JWT_SECRET = os.environ.get("AIP_E2E_KMS_JWT_SECRET", "dev_jwt_secret_key_32_chars_min")

#: ai-platform DB 단언용 DSN. 미설정 시 단언 픽스처에서 skip.
AIP_DB_DSN = os.environ.get(
    "AIP_E2E_DATABASE_URL",
    os.environ.get("AIP_DATABASE_URL", ""),
)

#: 비동기 동기화(webhook→job_queue→kms_sync) 완료 대기 한계 (초).
SYNC_TIMEOUT_SEC = float(os.environ.get("AIP_E2E_SYNC_TIMEOUT", "30"))


def _live_enabled() -> bool:
    return os.environ.get(LIVE_ENV_KEY, "") == "1"


# ---------------------------------------------------------------------------
# 게이트 ① — AIP_E2E_LIVE (collection 시점 skip, 조용한 통과 금지)
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(config, items):
    """e2e 패키지 아이템에 라이브 게이트 적용.

    AIP_E2E_LIVE 미설정 시: skip 마커 + 명시적 사유 부착.
    이렇게 하면 'collected N, skipped N' 로 **명시적으로** 드러나며
    조용히 통과(pass)하지 않는다.
    """
    if _live_enabled():
        return
    skip_live = pytest.mark.skip(
        reason=(
            f"{LIVE_ENV_KEY} 미설정 — 라이브 3-서비스 E2E 생략. "
            f"실행하려면 KMS/ai-platform/docforge 컨테이너 + OCR:5052/임베딩:8103 기동 후 "
            f"{LIVE_ENV_KEY}=1 설정. (조용한 통과 아님: 명시적 skip)"
        )
    )
    e2e_root = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(e2e_root):
            item.add_marker(skip_live)


# ---------------------------------------------------------------------------
# 게이트 ② — 서비스 헬스 프리체크
# ---------------------------------------------------------------------------

def _ping(base_url: str, health_path: str, timeout: float = 3.0) -> tuple[bool, str]:
    import httpx

    url = base_url.rstrip("/") + health_path
    try:
        resp = httpx.get(url, timeout=timeout)
        if resp.status_code < 500:
            return True, f"{url} -> {resp.status_code}"
        return False, f"{url} -> {resp.status_code} (5xx)"
    except Exception as exc:  # noqa: BLE001 — 네트워크 실패는 사유로 환원
        return False, f"{url} unreachable: {type(exc).__name__}: {exc}"


@pytest.fixture(scope="session")
def live_services() -> dict[str, str]:
    """3-서비스 헬스 프리체크. 하나라도 다운이면 skip + 다운 서비스 사유.

    반환: {service_name: base_url}
    """
    base_urls: dict[str, str] = {}
    down: list[str] = []
    for name, base_url, health_path in DEFAULT_SERVICES:
        ok, detail = _ping(base_url, health_path)
        base_urls[name] = base_url
        if not ok:
            down.append(f"{name}({detail})")
    if down:
        pytest.skip(
            "라이브 서비스 헬스 프리체크 실패 — 다운: " + "; ".join(down)
            + " (조용한 통과 아님: 명시적 skip)"
        )
    return base_urls


# ---------------------------------------------------------------------------
# KMS JWT (dev) — bff/api 공유 시크릿이 아니라 KMS dev 시크릿
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def kms_jwt() -> str:
    """KMS 인증용 dev JWT(HS256). EDITOR 권한(배치 POST 게이트 통과)."""
    try:
        import jwt as pyjwt
    except ImportError:  # pragma: no cover - 환경 의존
        pytest.skip("PyJWT 미설치 — KMS JWT 발급 불가 (pip install pyjwt)")
    now = int(time.time())
    payload = {
        "sub": "e2e-editor",
        "role": "EDITOR",
        "iat": now,
        "exp": now + 3600,
    }
    return pyjwt.encode(payload, KMS_JWT_SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# HTTP 클라이언트 (KMS / ai-platform)
# ---------------------------------------------------------------------------

@pytest.fixture
async def kms_client(live_services):
    import httpx

    base = live_services["kms"]
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        yield client


@pytest.fixture
async def aip_client(live_services):
    import httpx

    base = live_services["ai-platform"]
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        yield client


# ---------------------------------------------------------------------------
# ai-platform DB 단언 커넥션 (asyncpg)
# ---------------------------------------------------------------------------

def _to_asyncpg_dsn(dsn: str) -> str:
    """SQLAlchemy 스타일 DSN(+asyncpg)을 순수 asyncpg DSN으로 정규화."""
    return dsn.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


@pytest.fixture
async def aip_db():
    """ai-platform documents/chunks 단언용 asyncpg 커넥션.

    DSN 미설정 시 skip + 사유 (조용한 통과 금지).
    """
    if not AIP_DB_DSN:
        pytest.skip(
            "AIP_E2E_DATABASE_URL / AIP_DATABASE_URL 미설정 — DB 단언 불가 "
            "(조용한 통과 아님: 명시적 skip)"
        )
    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        pytest.skip("asyncpg 미설치 — DB 단언 불가")
    conn = await asyncpg.connect(_to_asyncpg_dsn(AIP_DB_DSN))
    try:
        yield conn
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 테스트 격리 cleanup — 생성한 external_id 추적 후 teardown 삭제
# ---------------------------------------------------------------------------

@pytest.fixture
async def cleanup_external_ids(aip_db):
    """테스트가 생성한 external_id 를 등록하면 teardown 에서 documents/chunks 삭제.

    사용:
        cleanup_external_ids(kms_doc_id)
    """
    registered: list[str] = []

    def _register(external_id: str) -> str:
        registered.append(external_id)
        return external_id

    yield _register

    for ext in registered:
        rows = await aip_db.fetch(
            "SELECT id FROM documents WHERE external_id = $1", ext
        )
        for row in rows:
            await aip_db.execute(
                "DELETE FROM document_chunks WHERE document_id = $1", row["id"]
            )
        await aip_db.execute("DELETE FROM documents WHERE external_id = $1", ext)
