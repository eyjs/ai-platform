"""HttpEmbeddingProvider 타임아웃/커넥션 풀 설정 테스트."""

import asyncio

import httpx
import pytest

from src.infrastructure.providers.embedding.http_embedding import HttpEmbeddingProvider


def test_default_timeout():
    """기본 타임아웃이 15초인지 확인."""
    provider = HttpEmbeddingProvider(base_url="http://localhost:8103")
    timeout = provider._client.timeout
    assert timeout.read == 15.0
    assert timeout.connect == 5.0


def test_custom_timeout():
    """커스텀 타임아웃이 올바르게 적용되는지 확인."""
    provider = HttpEmbeddingProvider(
        base_url="http://localhost:8103",
        timeout=10.0,
        connect_timeout=3.0,
    )
    timeout = provider._client.timeout
    assert timeout.read == 10.0
    assert timeout.connect == 3.0


def test_connection_pool_limits():
    """커넥션 풀이 max_concurrent와 동일하게 설정되는지 확인."""
    provider = HttpEmbeddingProvider(
        base_url="http://localhost:8103",
        max_concurrent=10,
    )
    # httpx.Limits가 올바르게 전달되었는지 확인
    pool_limits = provider._client._transport._pool._max_connections
    assert pool_limits == 10


def test_base_url_trailing_slash():
    """base_url 뒤의 슬래시가 제거되는지 확인."""
    provider = HttpEmbeddingProvider(base_url="http://localhost:8103/")
    assert provider._base_url == "http://localhost:8103"


def test_dimension_property():
    """dimension 프로퍼티가 올바르게 동작하는지 확인."""
    provider = HttpEmbeddingProvider(
        base_url="http://localhost:8103",
        dimension=768,
    )
    assert provider.dimension == 768
