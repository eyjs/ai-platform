"""GET /api/health/llm-engines — LLM 서빙 현황 집계.

이 엔드포인트의 존재 이유는 "설정에 적힌 것"과 "실제로 서빙 중인 것"이 어긋나는 걸
관리자가 보게 하는 것이다. 그래서 여기서 고정해야 할 성질은 목록의 예쁨이 아니라:

1. 못 읽었으면 못 읽었다고 한다 — 하드코딩 목록으로 절대 메우지 않는다.
   (8104가 7일간 /health 200을 주며 생성은 전건 실패했던 사고의 교훈)
2. 감시 대상이 아닌 것과 죽은 것을 구분한다 (up 3상태 보존).
3. 배선 규칙(build_llm_probe_targets)과 같은 것만 센다 — 더 큰 목록은 죽은 설정이 된다.
"""

from unittest.mock import MagicMock

import httpx
import pytest

from src.domain.models import UserRole
from src.gateway.routes import public

DGX_URL = "http://dgx:11434"

_TAGS_BODY = {
    "models": [
        {
            "name": "qwen3.6:35b-a3b",
            "details": {"parameter_size": "36.0B", "context_length": 262144},
            "capabilities": ["vision", "completion", "tools", "thinking"],
        },
        {
            "name": "qwen2.5vl:7b",
            "details": {"parameter_size": "7.6B", "context_length": 128000},
            "capabilities": ["vision", "completion"],
        },
    ],
}


def _settings(**kw):
    s = MagicMock()
    s.dgx_llm_url = kw.get("dgx_url", DGX_URL)
    s.dgx_main_model = kw.get("dgx_main_model", "qwen3.6:35b-a3b")
    s.dgx_local_fallback = kw.get("fallback", True)
    s.dgx_report_model = kw.get("dgx_report_model", "")
    s.dgx_router_model = kw.get("dgx_router_model", "")
    s.dgx_orchestrator_model = kw.get("dgx_orchestrator_model", "")
    s.dgx_fortune_model = kw.get("dgx_fortune_model", "")
    # 운영 실측 배선: main·fortune=8106, router=8105, report=8104, orchestrator 미설정
    s.main_llm_server_url = kw.get("main_url", "http://mlx:8106")
    s.router_llm_server_url = kw.get("router_url", "http://mlx:8105")
    s.report_llm_server_url = kw.get("report_url", "http://mlx:8104")
    s.fortune_llm_server_url = kw.get("fortune_url", "http://mlx:8106")
    s.orchestrator_server_url = kw.get("orchestrator_url", "")
    return s


def _link_status(**kw):
    return kw or {
        "llm:dgx": {"up": True, "checked_at": 1784173221.5, "detail": "generate ok", "latency_ms": 412.0},
        "llm:local:main": {"up": True, "checked_at": 1784173221.5, "detail": "generate ok", "latency_ms": 88.0},
        "llm:local:router": {"up": True, "checked_at": 1784173221.5, "detail": "generate ok", "latency_ms": 90.0},
        "llm:local:report": {"up": False, "checked_at": 1784173221.5, "detail": "http 500", "latency_ms": 30.0},
        # kms/docforge는 LLM 엔진이 아니다 — 이 엔드포인트에 새어 나오면 안 된다
        "kms": {"up": True, "checked_at": 1784173221.5, "detail": "ok", "latency_ms": 5.0},
    }


def _handler(tags_status=200, mlx_status=200, mlx_body=None):
    """DGX /api/tags 와 MLX /v1/models 를 흉내내는 트랜스포트 핸들러."""

    def _h(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            if tags_status != 200:
                return httpx.Response(tags_status, text="boom")
            return httpx.Response(200, json=_TAGS_BODY)
        if request.url.path == "/v1/models":
            if mlx_status != 200:
                return httpx.Response(mlx_status, text="boom")
            port = request.url.port
            body = mlx_body if mlx_body is not None else {
                "data": [{"id": f"mlx-community/Model-{port}"}],
            }
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return _h


@pytest.fixture
def call(monkeypatch):
    """엔드포인트를 실제 파싱 경로 그대로 호출한다 (트랜스포트만 가짜)."""

    async def _call(settings=None, links=None, handler=None, role=UserRole.ADMIN):
        async def _auth(_request):
            ctx = MagicMock()
            ctx.user_role = role
            return ctx

        monkeypatch.setattr(public, "_authenticate", _auth)
        state = MagicMock()
        state.settings = settings or _settings()
        monkeypatch.setattr(public, "_get_app_state", lambda _r: state)

        # 실제 클래스를 먼저 잡아둔다 — public.httpx는 httpx 모듈 그 자체라
        # 패치 후 httpx.AsyncClient를 부르면 자기 자신을 부른다(무한 재귀).
        h = handler or _handler()
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            public.httpx,
            "AsyncClient",
            lambda *a, **kw: real_client(transport=httpx.MockTransport(h)),
        )

        request = MagicMock()
        request.app.state.link_monitor.status = _link_status() if links is None else links
        return await public.health_llm_engines(request)

    return _call


# --- 권한 게이트 (형제 /health/hardware와 동일해야 한다) ---


@pytest.mark.parametrize("role", [UserRole.VIEWER, UserRole.EDITOR, UserRole.APPROVER])
async def test_non_admin_is_rejected(call, role):
    with pytest.raises(public.HTTPException) as e:
        await call(role=role)
    assert e.value.status_code == 403


# --- 현행 운영 구성 ---


async def test_dgx_catalog_is_runtime_truth(call):
    r = await call()
    assert r["dgx"]["configured"] is True
    assert r["dgx"]["modelsError"] is None
    assert [m["name"] for m in r["dgx"]["models"]] == ["qwen3.6:35b-a3b", "qwen2.5vl:7b"]
    default, vl = r["dgx"]["models"]
    assert default["isDefault"] is True
    assert default["parameterSize"] == "36.0B"
    assert default["contextLength"] == 262144
    assert vl["isDefault"] is False
    # tools 미보유를 UI가 표시할 수 있어야 한다 — capabilities를 접지 않는 이유
    assert "tools" not in vl["capabilities"]
    assert "tools" in default["capabilities"]


async def test_roles_sharing_one_url_collapse_into_one_engine(call):
    """엔진은 프로세스 단위다 — main·fortune이 8106을 공유하면 엔진 하나에 역할 둘."""
    r = await call()
    engines = {e["url"]: e for e in r["mlx"]["engines"]}
    assert set(engines) == {"http://mlx:8106", "http://mlx:8105", "http://mlx:8104"}
    assert engines["http://mlx:8106"]["roles"] == ["main", "fortune"]
    # orchestrator는 전용 URL이 없으면 router 서버를 쓴다(ProviderFactory) — 역할만 붙는다
    assert engines["http://mlx:8105"]["roles"] == ["router", "orchestrator"]


async def test_mlx_model_comes_from_the_server_not_settings(call):
    """MLX는 프로세스당 한 모델이라 요청 모델명을 무시한다 → 로드된 id가 진실."""
    r = await call()
    engines = {e["url"]: e for e in r["mlx"]["engines"]}
    assert engines["http://mlx:8106"]["model"] == "mlx-community/Model-8106"
    assert engines["http://mlx:8106"]["modelError"] is None


async def test_link_status_maps_to_first_role_key(call):
    """링크 키는 url을 처음 차지한 역할로 만들어진다(build_llm_probe_targets)."""
    r = await call()
    engines = {e["url"]: e for e in r["mlx"]["engines"]}
    assert engines["http://mlx:8106"]["link"] == {
        "up": True, "checkedAt": 1784173221.5, "detail": "generate ok", "latencyMs": 88.0,
    }
    assert engines["http://mlx:8104"]["link"]["up"] is False
    assert r["dgx"]["link"] == {
        "up": True, "checkedAt": 1784173221.5, "detail": "generate ok", "latencyMs": 412.0,
    }


async def test_non_llm_links_do_not_leak(call):
    """kms·docforge는 LLM 엔진이 아니다."""
    r = await call()
    assert "kms" not in str(r["mlx"]) and "kms" not in str(r["dgx"]["link"])


async def test_fallback_wiring_is_reported(call):
    r = await call()
    assert r["fallbackEnabled"] is True
    # providerMode 키는 레거시(프론트 대시보드 계약)지만 값은 이제 폴백 백엔드다 —
    # provider_mode 설정은 2026-07-16 상용 퇴역과 함께 사라졌다. MLX URL 이 배선돼 있으므로 "mlx".
    assert r["providerMode"] == "mlx"
    assert r["dgx"]["roleOverrides"] == {
        "report": "", "router": "", "orchestration": "", "fortune": "",
    }


async def test_provider_mode_reports_fallback_backend_not_a_dead_switch(call):
    """폴백을 끄면 "떨어질 곳이 없다"고 말해야 한다 — 고정 문자열이면 거짓말이 된다."""
    r = await call(settings=_settings(fallback=False))
    assert r["providerMode"] == "none"


async def test_provider_mode_is_ollama_without_mlx_url(call):
    """MLX URL 이 없으면 폴백은 ollama_host 로 흐른다."""
    r = await call(settings=_settings(main_url=""))
    assert r["providerMode"] == "ollama"


async def test_role_override_is_surfaced_when_set(call):
    r = await call(settings=_settings(dgx_report_model="qwen3:14b"))
    assert r["dgx"]["roleOverrides"]["report"] == "qwen3:14b"


# --- 3상태 보존 ---


async def test_unchecked_link_stays_null_not_false(call):
    """None을 false로 접으면 '아직 안 봤다'와 '죽었다'가 같아 보인다."""
    r = await call(links={"llm:dgx": {"up": None, "checked_at": None, "detail": "unchecked"}})
    assert r["dgx"]["link"]["up"] is None


async def test_unmonitored_engine_is_not_down(call):
    """감시 목록에 없는 엔진은 down이 아니라 unmonitored다."""
    r = await call(links={})
    assert r["dgx"]["link"] == {"up": None, "checkedAt": None, "detail": "unmonitored", "latencyMs": None}
    for e in r["mlx"]["engines"]:
        assert e["link"] == {"up": None, "checkedAt": None, "detail": "unmonitored", "latencyMs": None}


# --- 실패는 실패로 보고한다 (하드코딩 폴백 금지) ---


async def test_dgx_unconfigured_never_fabricates_models(call):
    r = await call(settings=_settings(dgx_url=""))
    assert r["dgx"]["configured"] is False
    assert r["dgx"]["models"] == []
    assert "미설정" in r["dgx"]["modelsError"]


async def test_dgx_tags_failure_yields_error_not_a_fake_list(call):
    r = await call(handler=_handler(tags_status=500))
    assert r["dgx"]["models"] == []
    assert "500" in r["dgx"]["modelsError"]
    # 한 대의 실패가 나머지를 가리면 안 된다
    assert len(r["mlx"]["engines"]) == 3


async def test_mlx_failure_is_isolated_to_that_engine(call):
    r = await call(handler=_handler(mlx_status=503))
    for e in r["mlx"]["engines"]:
        assert e["model"] is None
        assert "503" in e["modelError"]
    # MLX가 전부 죽어도 DGX 카탈로그는 나와야 한다
    assert len(r["dgx"]["models"]) == 2


async def test_mlx_serving_nothing_is_reported(call):
    r = await call(handler=_handler(mlx_body={"data": []}))
    assert all(e["model"] is None for e in r["mlx"]["engines"])
    assert all(e["modelError"] == "서빙 중인 모델 없음" for e in r["mlx"]["engines"])


# --- 배선 규칙 정합 ---


async def test_local_engines_hidden_when_fallback_off(call):
    """폴백이 꺼지면 로컬 MLX는 불리지 않는다 — 세면 죽은 설정을 살아 보이게 한다."""
    r = await call(settings=_settings(fallback=False))
    assert r["mlx"]["engines"] == []
    assert r["fallbackEnabled"] is False


async def test_local_engines_shown_without_dgx(call):
    """DGX가 없으면 로컬이 primary다 — 폴백 플래그와 무관하게 보여야 한다."""
    r = await call(settings=_settings(dgx_url="", fallback=False))
    assert len(r["mlx"]["engines"]) == 3
