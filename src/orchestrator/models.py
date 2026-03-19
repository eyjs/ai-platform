"""Orchestrator 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrchestratorResult:
    """MasterOrchestrator.route() 반환값."""

    selected_profile_id: str
    reason: str
    is_general_response: bool = False
    general_message: str = ""
    should_resume_workflow: bool = False
    paused_state: dict | None = None
    is_continuation: bool = False


@dataclass
class TenantConfig:
    """테넌트 설정."""

    id: str
    name: str
    orchestrator_enabled: bool = True
    default_chatbot_id: str | None = None
    is_active: bool = True
