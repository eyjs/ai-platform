"""공개 엔드포인트: /health, /profiles (인증 불필요).

/health/hardware, /health/llm-engines는 인프라 정보를 노출하므로 ADMIN 인증이 필요하다.
"""

import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Request

from src.domain.models import UserRole
from src.gateway.routes.helpers import APP_VERSION, _authenticate, _get_app_state

router = APIRouter()

# (표시명, settings 속성). 호스트 MLX 서버 /health를 폴링해 GPU·CPU·메모리 집계.
_HW_SERVERS = [
    ("main_llm", "main_llm_server_url"),
    ("router_llm", "router_llm_server_url"),
    ("report_llm", "report_llm_server_url"),
    ("fortune_llm", "fortune_llm_server_url"),
    ("embedding", "embedding_server_url"),
    ("reranker", "reranker_server_url"),
]


@router.get("/health")
async def health(request: Request):
    state = _get_app_state(request)
    payload = {
        "status": "ok",
        "version": APP_VERSION,
        "provider_mode": state.settings.provider_mode.value,
        "profiles_loaded": state.profile_store.profile_count,
    }
    # 내부 링크(KMS·DocForge) 최신 상태 — 상시 연결 원칙의 외부 관측점
    link_monitor = getattr(request.app.state, "link_monitor", None)
    if link_monitor:
        payload["links"] = link_monitor.status
    # 전역 동시 실행 상한 — 상한이 실제로 물리는지(rejected_total) 밖에서 보이게 한다.
    # 이 값이 죽은 설정이었을 때 아무도 용량 한계를 몰랐다는 게 문제의 시작이었다.
    gate = getattr(state, "concurrency_gate", None)
    if gate:
        payload["concurrency"] = gate.snapshot()
    return payload


@router.get("/health/hardware")
async def health_hardware(request: Request):
    """하드웨어 모니터링 — MLX 서버 /health 동시 폴링 → GPU 메모리·호스트 CPU/메모리 집계."""
    # 인프라 정보 노출 → ADMIN 전용 (형제 admin 엔드포인트와 게이트 일치).
    user_ctx = await _authenticate(request)
    if user_ctx.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="ADMIN 권한이 필요합니다")
    settings = _get_app_state(request).settings

    seen: dict[str, str] = {}  # url → 표시명 (중복 url 제거)
    for name, attr in _HW_SERVERS:
        url = (getattr(settings, attr, "") or "").rstrip("/")
        if url and url not in seen:
            seen[url] = name

    async def _probe(url: str, name: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                data = (await client.get(f"{url}/health")).json()
            return {
                "name": name, "url": url, "status": data.get("status", "unknown"),
                "model": data.get("model"), "gpu_active_mb": data.get("gpu_active_mb"),
                "host_cpu_pct": data.get("host_cpu_pct"),
                "host_mem_used_gb": data.get("host_mem_used_gb"),
                "host_mem_total_gb": data.get("host_mem_total_gb"),
                "host_mem_pct": data.get("host_mem_pct"),
            }
        except Exception as exc:
            return {"name": name, "url": url, "status": "unreachable", "error": str(exc)[:80]}

    results = await asyncio.gather(*[_probe(u, n) for u, n in seen.items()])
    host = next((r for r in results if r.get("host_cpu_pct") is not None), {})
    total_gpu = sum(r.get("gpu_active_mb") or 0 for r in results)

    return {
        "servers": results,
        "host": {
            "cpu_pct": host.get("host_cpu_pct"),
            "mem_used_gb": host.get("host_mem_used_gb"),
            "mem_total_gb": host.get("host_mem_total_gb"),
            "mem_pct": host.get("host_mem_pct"),
        },
        "gpu_total_mb": round(total_gpu, 1),
    }


# (역할, settings 속성). build_llm_probe_targets(link_monitor)와 같은 순서 — 이 순서가
# 링크 키(llm:local:{첫 역할})를 정하므로 어긋나면 상태 조회가 빗나간다.
_MLX_ROLE_SERVERS = [
    ("main", "main_llm_server_url"),
    ("router", "router_llm_server_url"),
    ("report", "report_llm_server_url"),
    ("fortune", "fortune_llm_server_url"),
    ("orchestrator", "orchestrator_server_url"),
]

# DGX 역할별 모델 오버라이드 (ProviderFactory._dgx_model_for와 키 일치).
# 전부 ""가 정상 — 역할마다 다른 모델을 주면 ollama 상주 한계로 evict↔reload가 돈다.
_DGX_ROLE_OVERRIDES = [
    ("report", "dgx_report_model"),
    ("router", "dgx_router_model"),
    ("orchestration", "dgx_orchestrator_model"),
    ("fortune", "dgx_fortune_model"),
]

_ENGINE_TIMEOUT = 4.0


def _link_of(link_status: dict, key: str) -> dict:
    """LinkMonitor 스냅샷에서 링크 하나를 UI 계약(camelCase)으로 옮긴다.

    up의 3상태(True/False/None=미점검)를 그대로 보존한다 — None을 false로 접으면
    "아직 안 봤다"와 "죽었다"가 같아 보여 오탐 경보가 된다.
    감시 대상이 아닌 엔진(키 부재)도 down이 아니라 unmonitored다.
    """
    s = link_status.get(key)
    if s is None:
        return {"up": None, "checkedAt": None, "detail": "unmonitored"}
    return {"up": s.get("up"), "checkedAt": s.get("checked_at"), "detail": s.get("detail")}


async def _fetch_dgx_models(
    client: httpx.AsyncClient, base_url: str, default_model: str,
) -> tuple[list[dict], str | None]:
    """DGX(ollama) 실제 서빙 목록. 실패 시 (빈 목록, 사유) — 하드코딩 폴백은 없다.

    관리자가 보는 목록이 실제와 다르면 없는 모델을 고르게 된다. 못 읽었을 땐
    그럴듯한 목록을 지어내는 것보다 '못 읽었다'고 말하는 편이 안전하다.
    """
    try:
        resp = await client.get(f"{base_url}/api/tags")
        if resp.status_code != 200:
            return [], f"http {resp.status_code}: {resp.text[:80]}"
        raw = resp.json().get("models") or []
    except Exception as exc:
        return [], f"{type(exc).__name__}: {str(exc)[:80]}"

    models = []
    for m in raw:
        name = m.get("name")
        details = m.get("details") or {}
        models.append({
            "name": name,
            "parameterSize": details.get("parameter_size"),
            "contextLength": details.get("context_length"),
            # tools 미보유 모델(예: qwen2.5vl:7b)이 섞여 있다 — 도구 호출 불가를 UI가
            # 표시할 수 있도록 원본 capabilities를 그대로 넘긴다.
            "capabilities": m.get("capabilities") or [],
            "isDefault": bool(default_model) and name == default_model,
        })
    return models, None


async def _fetch_mlx_model(
    client: httpx.AsyncClient, url: str,
) -> tuple[str | None, str | None]:
    """MLX 서버가 지금 물고 있는 모델 하나. 실패 시 (None, 사유).

    MLX 서버는 프로세스당 정확히 한 모델만 서빙해 요청 모델명을 무시한다
    (LinkMonitor의 GenerateProbe가 model을 안 채우는 이유와 같다). 그래서 설정에
    적힌 이름이 아니라 서버가 실제로 로드한 id를 진실로 삼는다.
    """
    try:
        resp = await client.get(f"{url}/v1/models")
        if resp.status_code != 200:
            return None, f"http {resp.status_code}: {resp.text[:80]}"
        data = resp.json().get("data") or []
        if not data:
            return None, "서빙 중인 모델 없음"
        return data[0].get("id"), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:80]}"


@router.get("/health/llm-engines")
async def health_llm_engines(request: Request):
    """LLM 서빙 현황 — DGX Spark 모델 카탈로그 + 호스트 MLX 엔진 + 링크 생존.

    관리자 대시보드 두 탭(호스트 MLX / DGX)의 단일 진실원천. 모델 목록은 전부 런타임
    조회이며 설정에 적힌 이름을 믿지 않는다 — 설정과 실제 서빙이 어긋나는 것이야말로
    여기서 잡아야 할 사고다(8104가 /health 200을 주며 생성은 전건 실패했던 건처럼).
    """
    # 인프라 정보 노출 → ADMIN 전용 (형제 /health/hardware와 게이트 일치).
    user_ctx = await _authenticate(request)
    if user_ctx.user_role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="ADMIN 권한이 필요합니다")
    settings = _get_app_state(request).settings

    link_monitor = getattr(request.app.state, "link_monitor", None)
    link_status = link_monitor.status if link_monitor else {}

    dgx_url = (getattr(settings, "dgx_llm_url", "") or "").rstrip("/")
    default_model = getattr(settings, "dgx_main_model", "") or ""
    fallback_enabled = bool(getattr(settings, "dgx_local_fallback", False))

    # MLX 배선 규칙은 build_llm_probe_targets와 동일하게 — DGX가 없어 로컬이 primary이거나,
    # 폴백이 켜져 실제로 불릴 때만 엔진으로 친다. 안 쓰는 서버까지 세면 죽은 설정이 된다.
    local_wired = not dgx_url or fallback_enabled

    engines: list[dict] = []
    if local_wired:
        by_url: dict[str, list[str]] = {}
        for role, attr in _MLX_ROLE_SERVERS:
            url = (getattr(settings, attr, "") or "").rstrip("/")
            # orchestration은 전용 서버가 없으면 router 서버로 폴백한다
            # (ProviderFactory.get_orchestration_llm). 이때 새 엔진이 생기는 게 아니라
            # 기존 router 엔진에 역할이 하나 더 붙는다.
            if not url and role == "orchestrator":
                url = (getattr(settings, "router_llm_server_url", "") or "").rstrip("/")
            if not url:
                continue
            # 여러 역할이 한 서버를 공유한다(main·fortune=8106, router·orchestrator=8105).
            # 엔진은 프로세스 단위이므로 url로 접고 역할을 모아 보여준다.
            by_url.setdefault(url, []).append(role)
        engines = [{"url": url, "roles": roles} for url, roles in by_url.items()]

    # 한 대가 죽어도 엔드포인트가 늘어지면 안 된다 → DGX 카탈로그와 MLX 전부 동시 조회.
    async with httpx.AsyncClient(timeout=_ENGINE_TIMEOUT) as client:
        tasks = [_fetch_mlx_model(client, e["url"]) for e in engines]
        if dgx_url:
            tasks.append(_fetch_dgx_models(client, dgx_url, default_model))
        results = await asyncio.gather(*tasks)

    if dgx_url:
        dgx_models, models_error = results[-1]
        mlx_results = results[:-1]
    else:
        dgx_models, models_error = [], "dgx_llm_url 미설정 — DGX 서빙이 배선되지 않았습니다"
        mlx_results = results

    mlx_engines = [
        {
            "roles": e["roles"],
            "url": e["url"],
            "model": model,
            # 링크 키는 그 url을 처음 차지한 역할로 만들어진다(build_llm_probe_targets).
            "link": _link_of(link_status, f"llm:local:{e['roles'][0]}"),
            "modelError": model_error,
        }
        for e, (model, model_error) in zip(engines, mlx_results)
    ]

    return {
        "providerMode": settings.provider_mode.value,
        # DGX 단절이 무엇으로 떨어지는지(로컬 MLX냐, 전면 정지냐)는 운영자가 알아야 한다.
        "fallbackEnabled": fallback_enabled,
        "dgx": {
            "configured": bool(dgx_url),
            "baseUrl": dgx_url,
            "defaultModel": default_model,
            "roleOverrides": {
                role: (getattr(settings, attr, "") or "") for role, attr in _DGX_ROLE_OVERRIDES
            },
            "link": _link_of(link_status, "llm:dgx"),
            "models": dgx_models,
            "modelsError": models_error,
        },
        "mlx": {"engines": mlx_engines},
    }


@router.get("/profiles")
async def list_profiles(request: Request):
    state = _get_app_state(request)
    profiles = await state.profile_store.list_all()
    return [
        {"id": p.id, "name": p.name, "mode": p.mode.value, "domains": p.domain_scopes}
        for p in profiles
    ]
