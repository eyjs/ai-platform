"""NullKmsClient + KmsGraphProtocol 검증."""

import pytest

from src.domain.protocols import KmsGraphProtocol
from src.services.null_kms_client import NullKmsClient
from src.services.kms_graph_client import KmsGraphClient


def test_null_kms_satisfies_protocol():
    client = NullKmsClient()
    assert isinstance(client, KmsGraphProtocol)


def test_real_kms_satisfies_protocol():
    client = KmsGraphClient("http://localhost", "key")
    assert isinstance(client, KmsGraphProtocol)


def test_null_kms_not_configured():
    client = NullKmsClient()
    assert client.is_configured is False


@pytest.mark.asyncio
async def test_null_kms_returns_none():
    client = NullKmsClient()
    result = await client.get_rag_context("any-id")
    assert result is None


@pytest.mark.asyncio
async def test_null_kms_close_is_noop():
    client = NullKmsClient()
    await client.close()
