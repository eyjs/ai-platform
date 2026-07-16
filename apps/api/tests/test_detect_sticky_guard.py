"""detect_sticky 노드의 이중 가드 통합 — 진단 V1 시나리오 재현.

진단 예시 그대로: 사주 워크플로우(생년월일 단계) 방치 → "자동차보험 대인배상 절차
알려줘" → 보험 질문이 사주 워크플로우에 강제 투입되던 사고.

가드 부재 시(구버전 배선) 기존 동작이 유지되는 것도 함께 고정한다 — 가드는 옵션이다.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.supervisor.graph import _create_detect_sticky
from src.supervisor.sticky_guard import StickyGuardConfig


CFG = StickyGuardConfig(ttl_seconds=7200, break_similarity=0.6, break_margin=0.15)

SAJU_VEC = [1.0, 0.0]
INSURANCE_VEC = [0.0, 1.0]
WEAK_VEC = [0.3, 0.3]


class _Profile:
    def __init__(self, pid, description="", domain_scopes=None):
        self.id = pid
        self.name = pid
        self.description = description
        self.domain_scopes = domain_scopes or []
        self.intent_hints = []


SAJU = _Profile("fortune-saju", description="사주 운세 풀이", domain_scopes=["saju"])
INSURANCE = _Profile("insurance-qa", description="자동차보험 약관", domain_scopes=["D01"])


def _embedder(mapping: dict[str, list[float]]):
    """텍스트 부분일치로 벡터를 돌려주는 가짜 임베더."""
    async def embed_batch(texts):
        out = []
        for t in texts:
            vec = WEAK_VEC
            for key, v in mapping.items():
                if key in t:
                    vec = v
                    break
            out.append(vec)
        return out

    provider = MagicMock()
    provider.embed_batch = AsyncMock(side_effect=embed_batch)
    return provider


def _engine(started_at: float, completed: bool = False, owner: str = "fortune-saju"):
    """owner 프로필에만 워크플로우 세션이 있는 엔진.

    실제로는 한 세션이 한 프로필에 속한다 — 모든 프로필에 세션을 주면 sticky가 풀린 뒤
    다음 프로필이 이어받아, 가드를 검증하는 게 아니라 목의 인공물을 보게 된다.
    """
    session = MagicMock()
    session.completed = completed
    session.started_at = started_at

    async def get_session(scoped_id: str):
        return session if scoped_id.endswith(f"::sub::{owner}") else None

    engine = MagicMock()
    engine.get_session = AsyncMock(side_effect=get_session)
    return engine


def _authorizer():
    a = MagicMock()
    a.is_delegation_allowed = MagicMock(return_value=True)
    return a


def _state(question: str):
    ctx = MagicMock()
    ctx.session_id = "s1"
    return {
        "allowed": {"fortune-saju", "insurance-qa"},
        "all_profiles": [SAJU, INSURANCE],
        "supervisor_id": "supervisor",
        "ctx": ctx,
        "question": question,
    }


EMBED_MAP = {"사주": SAJU_VEC, "보험": INSURANCE_VEC}


@pytest.mark.asyncio
async def test_hijack_is_blocked():
    """진단 재현: 사주 sticky 중 보험 질문 → sticky를 놓고 decompose로."""
    node = _create_detect_sticky(
        _authorizer(), _engine(time.time() - 60), CFG,
        _embedder({**EMBED_MAP, "자동차보험 대인배상": INSURANCE_VEC}),
    )
    out = await node(_state("자동차보험 대인배상 절차 알려줘"))
    assert out["sticky_profile"] is None


@pytest.mark.asyncio
async def test_mid_workflow_utterance_keeps_sticky():
    """★생년월일 답변은 sticky를 유지해야 한다 — 깨지면 실사고 재발."""
    node = _create_detect_sticky(
        _authorizer(), _engine(time.time() - 60), CFG, _embedder(EMBED_MAP),
    )
    out = await node(_state("1990-05-15"))
    assert out["sticky_profile"] == "fortune-saju", "중간 발화에서 sticky를 잃었다"


@pytest.mark.asyncio
async def test_stale_session_released():
    """TTL 초과 = 방치 → sticky 놓는다(질문이 사주여도)."""
    node = _create_detect_sticky(
        _authorizer(), _engine(time.time() - 10_000), CFG, _embedder(EMBED_MAP),
    )
    out = await node(_state("사주 봐줘"))
    assert out["sticky_profile"] is None


@pytest.mark.asyncio
async def test_completed_session_is_not_sticky():
    node = _create_detect_sticky(
        _authorizer(), _engine(time.time() - 60, completed=True), CFG, _embedder(EMBED_MAP),
    )
    assert (await node(_state("1990-05-15")))["sticky_profile"] is None


@pytest.mark.asyncio
async def test_no_guard_keeps_legacy_behavior():
    """가드 미배선(구버전) — 미완료 세션이면 무조건 sticky(기존 동작)."""
    node = _create_detect_sticky(_authorizer(), _engine(time.time() - 10_000))
    out = await node(_state("자동차보험 대인배상 절차 알려줘"))
    assert out["sticky_profile"] == "fortune-saju"


@pytest.mark.asyncio
async def test_embedding_failure_keeps_sticky():
    """가드가 터져도 sticky를 잃지 않는다 — 관측용 가드가 기능을 죽이면 안 된다."""
    broken = MagicMock()
    broken.embed_batch = AsyncMock(side_effect=RuntimeError("임베딩 다운"))
    node = _create_detect_sticky(_authorizer(), _engine(time.time() - 60), CFG, broken)
    out = await node(_state("자동차보험 대인배상 절차 알려줘"))
    assert out["sticky_profile"] == "fortune-saju"


@pytest.mark.asyncio
async def test_profile_signal_embedding_is_cached():
    """프로필 신호는 매 턴 재임베딩하지 않는다 — 캐시가 없으면 턴마다 N회 호출."""
    provider = _embedder(EMBED_MAP)
    node = _create_detect_sticky(_authorizer(), _engine(time.time() - 60), CFG, provider)
    await node(_state("1990-05-15"))
    first = provider.embed_batch.call_count
    await node(_state("1990-05-15"))
    # 두 번째 턴은 질문/프로필 텍스트가 같아 전부 캐시 히트여야 한다
    assert provider.embed_batch.call_count == first
