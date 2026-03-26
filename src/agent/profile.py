"""Backward-compat re-exports. Canonical: src.domain.agent_profile."""

from src.domain.agent_profile import (  # noqa: F401
    AgentProfile,
    HybridTrigger,
    IntentHint,
    ToolRef,
)
from src.domain.models import AgentMode, ResponsePolicy, SecurityLevel  # noqa: F401
