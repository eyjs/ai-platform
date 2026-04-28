"""테스트 공통 fixture.

Task 013: 상용 LLM 실 호출 차단 기계적 장치 추가.
"""

from __future__ import annotations

import os

import pytest

from src.locale.bundle import LocaleBundle, set_locale

# 상용 LLM 차단 대상 env keys
_COMMERCIAL_LLM_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "AIP_PROVIDER_ENABLE_ANTHROPIC",
    "AIP_PROVIDER_ENABLE_OPENAI",
)

# 차단 대상 호스트 패턴
_BLOCKED_LLM_HOSTS = (
    "api.anthropic.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
)


@pytest.fixture(autouse=True, scope="session")
def _init_locale():
    """테스트 세션 시작 시 로케일 번들을 초기화한다."""
    bundle = LocaleBundle.load("src/locale/ko.yaml")
    set_locale(bundle)


@pytest.fixture(autouse=True, scope="session")
def _strip_commercial_llm_env():
    """세션 스코프에서 상용 LLM 환경변수 제거.

    pytest 기본 monkeypatch 는 function scope 이므로 직접 저장/복원.
    """
    saved: dict[str, str] = {}
    for key in _COMMERCIAL_LLM_ENV_KEYS:
        if key in os.environ:
            saved[key] = os.environ[key]
            del os.environ[key]
    # 상용 Provider 는 stub 만 동작하도록 강제
    saved_mode = os.environ.pop("AIP_PROVIDER_MODE", None)
    os.environ["AIP_PROVIDER_MODE"] = "development"

    yield

    # 복원
    for key, val in saved.items():
        os.environ[key] = val
    if saved_mode is not None:
        os.environ["AIP_PROVIDER_MODE"] = saved_mode
    else:
        os.environ.pop("AIP_PROVIDER_MODE", None)


@pytest.fixture(autouse=True)
def _block_external_llm_http(monkeypatch):
    """httpx 의 AsyncClient/Client 요청 시 상용 LLM 도메인이면 즉시 실패.

    pytest-httpx 의존성 없이 경량 구현.
    """
    import httpx

    original_async_send = httpx.AsyncClient.send
    original_sync_send = httpx.Client.send

    def _check(url: str) -> None:
        host = str(url)
        for blocked in _BLOCKED_LLM_HOSTS:
            if blocked in host:
                raise RuntimeError(f"Outbound commercial LLM call blocked: {host}")

    async def _async_send(self, request, *args, **kwargs):
        _check(str(request.url))
        return await original_async_send(self, request, *args, **kwargs)

    def _sync_send(self, request, *args, **kwargs):
        _check(str(request.url))
        return original_sync_send(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", _async_send)
    monkeypatch.setattr(httpx.Client, "send", _sync_send)
