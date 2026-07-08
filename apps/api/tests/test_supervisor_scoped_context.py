"""derive_scoped_context 단위 테스트 (P0-5)."""

from __future__ import annotations

import copy

from src.domain.agent_context import AgentContext
from src.supervisor.models import DelegationStep
from src.supervisor.scoped_context import derive_scoped_context


def _make_parent_ctx() -> AgentContext:
    return AgentContext(
        session_id="parent-session-1",
        user_id="user-1",
        user_role="EDITOR",
        conversation_history=["turn1", "turn2", "turn3", "turn4"],
        prior_doc_ids=["doc-parent-1"],
        metadata={"company_id": "acme", "secret_internal": "should-not-leak"},
        tenant_id="tenant-1",
    )


def test_기본_history_미포함_및_상속():
    parent_ctx = _make_parent_ctx()
    parent_snapshot = copy.deepcopy(parent_ctx)
    step = DelegationStep(profile="rag", subquery="서브쿼리")

    scoped = derive_scoped_context(parent_ctx, step)

    assert scoped.conversation_history == []
    assert scoped.user_id == parent_ctx.user_id
    assert scoped.user_role == parent_ctx.user_role
    assert scoped.tenant_id == parent_ctx.tenant_id
    # 원본 parent_ctx는 변경되지 않아야 한다(불변성).
    assert parent_ctx.conversation_history == parent_snapshot.conversation_history
    assert parent_ctx.metadata == parent_snapshot.metadata
    assert parent_ctx.session_id == parent_snapshot.session_id


def test_include_history_true면_최근_N턴만_복사():
    parent_ctx = _make_parent_ctx()
    step = DelegationStep(
        profile="rag",
        subquery="서브쿼리",
        scope_hint={"include_history": True},
    )

    scoped = derive_scoped_context(parent_ctx, step)

    assert scoped.conversation_history == ["turn3", "turn4"]


def test_scope_hint_doc_ids가_prior_doc_ids에_반영():
    parent_ctx = _make_parent_ctx()
    step = DelegationStep(
        profile="rag",
        subquery="서브쿼리",
        scope_hint={"doc_ids": ["doc-a", "doc-b"]},
    )

    scoped = derive_scoped_context(parent_ctx, step)

    assert scoped.prior_doc_ids == ["doc-a", "doc-b"]


def test_doc_ids_미지정시_prior_doc_ids_빈리스트():
    parent_ctx = _make_parent_ctx()
    step = DelegationStep(profile="rag", subquery="서브쿼리")

    scoped = derive_scoped_context(parent_ctx, step)

    assert scoped.prior_doc_ids == []


def test_metadata_화이트리스트_비허용_키는_누락():
    parent_ctx = _make_parent_ctx()
    step = DelegationStep(profile="rag", subquery="서브쿼리")

    scoped = derive_scoped_context(parent_ctx, step)

    assert scoped.metadata == {"company_id": "acme"}
    assert "secret_internal" not in scoped.metadata


def test_session_id가_부모와_구별됨():
    parent_ctx = _make_parent_ctx()
    step = DelegationStep(profile="rag-profile", subquery="서브쿼리")

    scoped = derive_scoped_context(parent_ctx, step)

    assert scoped.session_id != parent_ctx.session_id
    assert parent_ctx.session_id in scoped.session_id
    assert "rag-profile" in scoped.session_id
