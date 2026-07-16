"""부팅 시 LLM 설정↔배선 정합성 검사 (_check_llm_wiring_alignment).

배경: DGX 위임(2026-07-16) 이후 env에 남아 있으나 아무도 읽지 않는 설정이 생겼다.
설정이 살아 보이면 그걸 믿고 바꾼 사람이 "왜 아무 일도 안 일어나지"에서 시간을 태운다.
아키텍처 진단(2026-07-15)이 "죽은 설정 재발 방지"를 요구한 바로 그 부류다.

거짓 경고를 내지 않는 것이 이 검사의 생명이다 — 안 죽은 걸 죽었다고 하면 경고 전체가
무시된다. 그래서 "울려야 할 때"만큼 "울리면 안 될 때"도 같이 고정한다.
"""

from unittest.mock import MagicMock

import pytest

from src.bootstrap import _check_llm_wiring_alignment
from src.config import ProviderMode


def _settings(**kw):
    s = MagicMock()
    s.dgx_llm_url = kw.get("dgx_url", "")
    s.dgx_local_fallback = kw.get("fallback", True)
    s.dgx_report_model = kw.get("dgx_report_model", "")
    s.dgx_router_model = kw.get("dgx_router_model", "")
    s.dgx_orchestrator_model = kw.get("dgx_orchestrator_model", "")
    s.dgx_fortune_model = kw.get("dgx_fortune_model", "")
    s.main_llm_backend = kw.get("main_llm_backend", "")
    s.provider_mode = kw.get("provider_mode", ProviderMode.DEVELOPMENT)
    s.main_llm_server_url = kw.get("main_url", "http://mlx:8106")
    s.router_llm_server_url = kw.get("router_url", "")
    s.report_llm_server_url = kw.get("report_url", "")
    s.fortune_llm_server_url = kw.get("fortune_url", "")
    s.orchestrator_server_url = kw.get("orchestrator_url", "")
    s.ollama_host = kw.get("ollama_host", "http://localhost:11434")
    return s


def _events(caplog):
    return [r.getMessage() for r in caplog.records]


def _fired(caplog, event: str) -> bool:
    return any(event in m for m in _events(caplog))


def _hint(caplog, event: str) -> str:
    """구조화 로거는 kwargs를 _structured_data에 담는다."""
    for r in caplog.records:
        if event in r.getMessage():
            return str(r.__dict__.get("_structured_data", {}).get("hint", ""))
    return ""


# --- 현행 운영 구성: 무소음이어야 한다 ---


def test_current_production_wiring_is_silent(caplog):
    """DGX + 폴백 on + development + 로컬 URL 있음 = 지금 운영 구성.

    여기서 경고가 나면 매 부팅마다 거짓 경고가 찍혀 경고 전체가 무시된다.
    """
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="http://dgx:11434", fallback=True,
            provider_mode=ProviderMode.DEVELOPMENT, main_url="http://mlx:8106",
        ))
    assert _events(caplog) == []


def test_no_dgx_plain_local_is_silent(caplog):
    """DGX를 아예 안 쓰는 환경(CI·타 개발자)도 무소음."""
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(dgx_url="", main_url="http://mlx:8106"))
    assert _events(caplog) == []


# --- DGX가 가려버리는 설정 ---


def test_main_llm_backend_is_flagged_as_fallback_only(caplog):
    """폴백이 켜져 있어도 main_llm_backend는 primary(DGX)를 못 바꾼다."""
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="http://dgx:11434", fallback=True, main_llm_backend="anthropic",
        ))
    assert _fired(caplog, "llm_setting_shadowed_by_dgx")
    assert "폴백 base에만" in _hint(caplog, "llm_setting_shadowed_by_dgx")


def test_main_llm_backend_is_fully_dead_without_fallback(caplog):
    """폴백이 꺼지면 base 자체가 생성되지 않아 완전히 죽은 값이 된다."""
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="http://dgx:11434", fallback=False, main_llm_backend="anthropic",
            main_url="",
        ))
    assert _fired(caplog, "llm_setting_shadowed_by_dgx")
    # 폴백 on일 때와 문구가 달라야 한다 — "폴백에만 유효"와 "완전히 죽음"은 대응이 다르다
    assert "완전히 죽은 값" in _hint(caplog, "llm_setting_shadowed_by_dgx")


def test_provider_mode_anthropic_is_flagged(caplog):
    """provider_mode=anthropic으로 바꿔도 최종 답변은 DGX가 낸다 — 오해를 잡는다."""
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="http://dgx:11434", provider_mode=ProviderMode.ANTHROPIC,
        ))
    assert _fired(caplog, "provider_mode_no_longer_decides_main_llm")


def test_local_urls_unused_when_fallback_off(caplog):
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="http://dgx:11434", fallback=False,
            main_url="http://mlx:8106", report_url="http://mlx:8104",
        ))
    assert _fired(caplog, "local_llm_urls_unused")


def test_local_urls_not_flagged_when_fallback_on(caplog):
    """폴백이 켜져 있으면 그 URL들은 실제 배선이다 — 죽은 설정이 아니다."""
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="http://dgx:11434", fallback=True,
            main_url="http://mlx:8106", report_url="http://mlx:8104",
        ))
    assert not _fired(caplog, "local_llm_urls_unused")


# --- 명목뿐인 폴백 ---


def test_fallback_on_without_local_url_is_flagged(caplog):
    """폴백을 켰는데 로컬 URL이 없으면 ollama_host로 흘러 명목뿐이 된다.

    8104가 7일간 '폴백'이었지만 실제로는 죽어 있던 그 부류를 설정 단계에서 잡는다.
    """
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="http://dgx:11434", fallback=True, main_url="",
        ))
    assert _fired(caplog, "fallback_enabled_without_local_url")


# --- DGX 없이 남은 DGX 설정 ---


def test_orphan_dgx_settings_flagged(caplog):
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="", dgx_report_model="qwen3:14b",
        ))
    assert _fired(caplog, "dgx_settings_without_dgx_url")


def test_orphan_check_does_not_run_when_dgx_set(caplog):
    """DGX가 켜져 있으면 역할별 모델 오버라이드는 정상 설정이다."""
    with caplog.at_level("WARNING"):
        _check_llm_wiring_alignment(_settings(
            dgx_url="http://dgx:11434", dgx_report_model="qwen3:14b",
        ))
    assert not _fired(caplog, "dgx_settings_without_dgx_url")
