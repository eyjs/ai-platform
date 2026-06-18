"""T7: Anthropic Prompt Caching stub 구조 검증 (task-100 확장)."""

from __future__ import annotations

import logging
import pytest

from src.infrastructure.providers.llm.anthropic import AnthropicStubProvider, _warn_if_cacheable_too_small
from src.infrastructure.providers.base import ProviderCapability


def _make_provider(system_prefix: str = "") -> AnthropicStubProvider:
    """AnthropicStubProvider 생성."""
    import os
    os.environ["AIP_PROVIDER_ANTHROPIC_STUB_MODE"] = "echo"
    return AnthropicStubProvider(system_prefix=system_prefix)


# ── 기존 테스트 (회귀 보장) ────────────────────────────────────────────────────

def test_capability_supports_prompt_caching():
    """capability에 supports_prompt_caching=True."""
    provider = _make_provider()
    assert provider.capability.supports_prompt_caching is True


def test_build_system_blocks_with_prefix():
    """system_prefix가 있으면 cache_control 블록 포함 (하위호환: system → volatile)."""
    provider = _make_provider(system_prefix="Base instructions.")
    blocks = provider._build_system_blocks(system="Dynamic part.")

    assert len(blocks) == 2
    # 첫 번째: 캐시 가능한 prefix (cache_control 부여 — cacheable 마지막 블록)
    assert blocks[0] == {
        "type": "text",
        "text": "Base instructions.",
        "cache_control": {"type": "ephemeral"},
    }
    # 두 번째: volatile (기존 system 인자) — cache_control 없음
    assert blocks[1] == {
        "type": "text",
        "text": "Dynamic part.",
    }


def test_build_system_blocks_prefix_only():
    """system_prefix만 있으면 1블록."""
    provider = _make_provider(system_prefix="Base instructions.")
    blocks = provider._build_system_blocks(system="")

    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_build_system_blocks_system_only():
    """prefix 없이 system만 있으면 cache_control 없음."""
    provider = _make_provider(system_prefix="")
    blocks = provider._build_system_blocks(system="Dynamic.")

    assert len(blocks) == 1
    assert "cache_control" not in blocks[0]
    assert blocks[0]["text"] == "Dynamic."


def test_build_system_blocks_empty():
    """둘 다 없으면 빈 리스트."""
    provider = _make_provider(system_prefix="")
    blocks = provider._build_system_blocks(system="")
    assert blocks == []


async def test_stub_echo_mode_still_works():
    """echo 모드에서 기존 generate 동작 유지."""
    provider = _make_provider(system_prefix="Prefix.")
    result = await provider.generate("Hello World", system="Test")
    assert "[anthropic-stub] echo:" in result
    assert "Hello World" in result


# ── task-100 신규 테스트 ───────────────────────────────────────────────────────

def test_cacheable_volatile_two_block_structure():
    """cacheable_system + volatile_system → 2블록, cache_control 은 cacheable 에만."""
    provider = _make_provider(system_prefix="")
    blocks = provider._build_system_blocks(
        cacheable_system="페르소나+grounding 고정 텍스트.",
        volatile_system="오늘 날짜: 2026-06-18.",
    )

    assert len(blocks) == 2
    # cacheable 블록에 cache_control 있어야 함
    assert blocks[0]["text"] == "페르소나+grounding 고정 텍스트."
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    # volatile 블록에 cache_control 없어야 함
    assert blocks[1]["text"] == "오늘 날짜: 2026-06-18."
    assert "cache_control" not in blocks[1]


def test_cacheable_volatile_with_prefix_three_blocks():
    """system_prefix + cacheable_system + volatile_system → 3블록.
    cache_control 은 cacheable_system 블록(마지막 캐시 가능 블록)에만.
    """
    provider = _make_provider(system_prefix="공통 prefix.")
    blocks = provider._build_system_blocks(
        cacheable_system="페르소나 전문.",
        volatile_system="날짜 정보.",
    )

    assert len(blocks) == 3
    # system_prefix — cache_control 없음 (cacheable_system 이 뒤에 있으므로 여기엔 안 붙음)
    assert blocks[0]["text"] == "공통 prefix."
    assert "cache_control" not in blocks[0]
    # cacheable_system — cache_control 있음 (캐시 경계)
    assert blocks[1]["text"] == "페르소나 전문."
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    # volatile — cache_control 없음
    assert blocks[2]["text"] == "날짜 정보."
    assert "cache_control" not in blocks[2]


def test_volatile_only_no_cache_control():
    """volatile_system 만 지정 시 cache_control 없음 (prefix 없는 경우)."""
    provider = _make_provider(system_prefix="")
    blocks = provider._build_system_blocks(volatile_system="오늘은 수요일.")

    assert len(blocks) == 1
    assert "cache_control" not in blocks[0]
    assert blocks[0]["text"] == "오늘은 수요일."


def test_cacheable_only_single_block_with_cache():
    """cacheable_system 만 지정 시 1블록, cache_control 있음."""
    provider = _make_provider(system_prefix="")
    blocks = provider._build_system_blocks(cacheable_system="페르소나만.")

    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == "페르소나만."


def test_backward_compat_single_system_arg():
    """기존 단일 system 인자 호출 — volatile_system 으로 취급, cache_control 없음."""
    provider = _make_provider(system_prefix="")
    blocks = provider._build_system_blocks(system="레거시 호출.")

    assert len(blocks) == 1
    assert "cache_control" not in blocks[0]
    assert blocks[0]["text"] == "레거시 호출."


def test_backward_compat_prefix_plus_system():
    """system_prefix + 단일 system — prefix 에 cache_control, system 에 없음."""
    provider = _make_provider(system_prefix="고정 prefix.")
    blocks = provider._build_system_blocks(system="동적 system.")

    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]


def test_count_tokens_warning_hook(caplog):
    """cacheable 토큰 수 < 4096 시 경고 로그 발생."""
    with caplog.at_level(logging.WARNING, logger="src.infrastructure.providers.llm.anthropic"):
        _warn_if_cacheable_too_small("짧은 텍스트.")  # << 4096 토큰

    assert any("anthropic_cache_too_small" in r.message for r in caplog.records)


def test_count_tokens_no_warning_large_text(caplog):
    """cacheable 토큰 수 >= 4096 시 경고 없음."""
    large_text = "A" * (4096 * 4 + 100)  # char/4 기준 4096+ 토큰
    with caplog.at_level(logging.WARNING, logger="src.infrastructure.providers.llm.anthropic"):
        _warn_if_cacheable_too_small(large_text)

    assert not any("anthropic_cache_too_small" in r.message for r in caplog.records)


async def test_cacheable_volatile_generate_echo():
    """echo 모드에서 cacheable_system/volatile_system 인자 전달 시 generate 정상 동작."""
    provider = _make_provider(system_prefix="Prefix.")
    result = await provider.generate(
        "Hello",
        cacheable_system="페르소나 텍스트.",
        volatile_system="오늘 날짜.",
    )
    assert "[anthropic-stub] echo:" in result
    assert "Hello" in result


def test_all_empty_returns_empty_list():
    """모든 인자 비면 빈 리스트 (기존 {} 반환 동작과 일치)."""
    provider = _make_provider(system_prefix="")
    blocks = provider._build_system_blocks()
    assert blocks == []
