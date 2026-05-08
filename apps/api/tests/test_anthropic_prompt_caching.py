"""T7: Anthropic Prompt Caching stub 구조 검증."""

from __future__ import annotations

import pytest

from src.infrastructure.providers.llm.anthropic import AnthropicStubProvider
from src.infrastructure.providers.base import ProviderCapability


def _make_provider(system_prefix: str = "") -> AnthropicStubProvider:
    """AnthropicStubProvider 생성."""
    import os
    os.environ["AIP_PROVIDER_ANTHROPIC_STUB_MODE"] = "echo"
    return AnthropicStubProvider(system_prefix=system_prefix)


def test_capability_supports_prompt_caching():
    """capability에 supports_prompt_caching=True."""
    provider = _make_provider()
    assert provider.capability.supports_prompt_caching is True


def test_build_system_blocks_with_prefix():
    """system_prefix가 있으면 cache_control 블록 포함."""
    provider = _make_provider(system_prefix="Base instructions.")
    blocks = provider._build_system_blocks(system="Dynamic part.")

    assert len(blocks) == 2
    # 첫 번째: 캐시 가능한 prefix
    assert blocks[0] == {
        "type": "text",
        "text": "Base instructions.",
        "cache_control": {"type": "ephemeral"},
    }
    # 두 번째: 동적 시스템 프롬프트
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
