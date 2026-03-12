"""Fact Lookup Tool: FactStore chain resolution."""

import logging
from typing import Optional

from src.infrastructure.fact_store import FactStore
from src.router.execution_plan import SearchScope
from src.tools.base import AgentContext, ToolResult

logger = logging.getLogger(__name__)


class FactLookupTool:
    """구조화된 팩트 검색 도구 (ScopedTool)."""

    name = "fact_lookup"
    description = "구조화된 팩트 검색 + 체인 탐색"
    input_schema = {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "검색할 주제/엔티티"},
            "chain": {"type": "boolean", "description": "체인 탐색 여부", "default": False},
        },
        "required": ["subject"],
    }

    def __init__(self, fact_store: FactStore):
        self._fact_store = fact_store

    async def execute(
        self,
        params: dict,
        context: AgentContext,
        scope: SearchScope,
    ) -> ToolResult:
        subject = params.get("subject", "")
        if not subject:
            return ToolResult.fail("subject is required")

        domain_codes = scope.domain_codes if scope.domain_codes else None
        use_chain = params.get("chain", False)

        if use_chain:
            facts = await self._fact_store.chain_resolve(
                subject=subject,
                domain_codes=domain_codes,
                max_depth=3,
            )
        else:
            facts = await self._fact_store.search(
                query=subject,
                domain_codes=domain_codes,
                limit=10,
            )

        return ToolResult.ok(
            facts,
            method="fact_lookup",
            facts_found=len(facts),
            chain_used=use_chain,
        )
