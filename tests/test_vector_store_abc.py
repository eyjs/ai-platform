"""AbstractVectorStore ABC 준수 검증."""

import pytest

from src.infrastructure.providers.vector_store_base import AbstractVectorStore
from src.infrastructure.vector_store import VectorStore


def test_vector_store_is_subclass():
    """VectorStore가 AbstractVectorStore의 서브클래스여야 한다."""
    assert issubclass(VectorStore, AbstractVectorStore)


def test_incomplete_subclass_raises():
    """추상 메서드를 구현하지 않으면 TypeError가 발생해야 한다."""

    class BadStore(AbstractVectorStore):
        pass

    with pytest.raises(TypeError):
        BadStore()
