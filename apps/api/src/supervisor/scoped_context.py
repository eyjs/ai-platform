"""위임 스코프 컨텍스트 파생 (P0-5).

메인이 소유한 `AgentContext`를 서브에 그대로 넘기지 않고, 서브쿼리 수행에
필요한 최소 범위만 담은 새 `AgentContext`를 파생시키는 순수 함수를 제공한다.

최소권한 원칙(§6-4):
- 서브는 stateless이며 전체 대화 맥락을 알 필요가 없다. 기본은 서브쿼리만 수행.
- 보안 컨텍스트(user_id/user_role/tenant_id)는 부모에서 그대로 상속한다(하향만 허용,
  상승은 금지). 실제 접근 범위 제한은 서브 프로파일의 security_level_max가 담당한다.
- metadata는 화이트리스트 키만 복사한다(전체 복사 금지 — 불필요한 정보 유출 방지).
"""

from __future__ import annotations

from src.domain.agent_context import AgentContext
from src.supervisor.models import DelegationStep

# 서브 컨텍스트로 복사를 허용하는 metadata 키 화이트리스트.
# 근거: 아래 키들은 "어느 회사/테넌트 범위에서 검색해야 하는가"를 결정하는 스코프성
# 정보로, 서브가 서브쿼리를 정확히 수행하기 위해 반드시 필요하다. 그 외 키
# (예: 원본 대화 전체를 가리키는 메타, 내부 라우팅 상태 등)는 최소권한 원칙에 따라
# 서브에 노출하지 않는다.
METADATA_WHITELIST = ("company_id",)

# scope_hint.include_history=True일 때 복사할 최근 대화 턴 수(P0 기본값).
INCLUDED_HISTORY_TURNS = 2


def derive_scoped_context(parent_ctx: AgentContext, step: DelegationStep) -> AgentContext:
    """부모 `AgentContext`에서 서브에 넘길 최소 범위의 새 컨텍스트를 파생한다.

    순수 함수: 외부 IO 없음, `parent_ctx`를 변경하지 않고 항상 새 `AgentContext`를
    반환한다.
    """
    # 위임 트리 상관을 위한 자식 세션 식별자 파생.
    # 주의: 서브는 stateless이므로 이 id로 세션 메모리를 write하지 않는다(task-001 계약).
    # 트레이스/로그 상관용으로만 사용한다.
    scoped_session_id = f"{parent_ctx.session_id}::sub::{step.profile}"

    # 서브는 기본적으로 서브쿼리로 완결되므로 대화 이력은 미포함이 기본값.
    # scope_hint에 명시적으로 include_history=True가 있을 때만 최근 N턴을 복사한다.
    conversation_history: list = []
    if step.scope_hint.get("include_history"):
        conversation_history = list(parent_ctx.conversation_history[-INCLUDED_HISTORY_TURNS:])

    # 필요 문서만 전달(없으면 빈 리스트).
    prior_doc_ids = list(step.scope_hint.get("doc_ids", []))

    # metadata는 화이트리스트 키만 복사(전체 복사 금지).
    scoped_metadata = {
        key: parent_ctx.metadata[key] for key in METADATA_WHITELIST if key in parent_ctx.metadata
    }

    return AgentContext(
        session_id=scoped_session_id,
        # 보안 컨텍스트는 부모에서 그대로 상속(권한 상승 방지, 하향은 프로파일이 별도 제한).
        user_id=parent_ctx.user_id,
        user_role=parent_ctx.user_role,
        conversation_history=conversation_history,
        prior_doc_ids=prior_doc_ids,
        metadata=scoped_metadata,
        tenant_id=parent_ctx.tenant_id,
    )
