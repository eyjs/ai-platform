"""공개 엔드포인트: /health, /profiles (인증 불필요). /health/hardware는 인증 필요."""

import asyncio

import httpx
from fastapi import APIRouter, Request

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
    return {
        "status": "ok",
        "version": APP_VERSION,
        "provider_mode": state.settings.provider_mode.value,
        "profiles_loaded": state.profile_store.profile_count,
    }


@router.get("/health/hardware")
async def health_hardware(request: Request):
    """하드웨어 모니터링 — MLX 서버 /health 동시 폴링 → GPU 메모리·호스트 CPU/메모리 집계."""
    await _authenticate(request)  # 인증 필수 (인프라 정보 보호)
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


@router.get("/profiles")
async def list_profiles(request: Request):
    state = _get_app_state(request)
    profiles = await state.profile_store.list_all()
    return [
        {"id": p.id, "name": p.name, "mode": p.mode.value, "domains": p.domain_scopes}
        for p in profiles
    ]
