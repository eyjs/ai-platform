# ADR-004: Workflow Action Step Architecture

**Date**: 2026-05-07
**Status**: Accepted
**Deciders**: Pipeline Orchestrator + Planner + Reviewer

## Context

The Workflow Engine handles user input collection through input/select/confirm steps, but lacks the ability to submit collected data to external systems. Domain-specific chatbots (insurance, reservations, orders) need end-to-end flows where collected data is sent to external APIs and results are returned to users.

Key requirements:
1. External HTTP calls must be configurable via YAML only (no code changes)
2. Engine must become async to support non-blocking HTTP calls
3. Sessions must survive server restarts (currently in-memory dict)
4. Different workflows need different escape keywords

## Decision

### 1. Action Step as a New Step Type

Action steps are a new `WorkflowStep.type == "action"` handled in `_process_current_step`. The step carries its own endpoint, headers, payload template, and success/error messages. This follows the existing pattern where step behavior is determined by `step.type`.

**Alternative considered**: A separate "post-workflow hook" system. Rejected because it would create a parallel execution path outside the step-based engine, violating the sequential step model.

### 2. ActionClient as Injected Dependency

`ActionClient` wraps `httpx.AsyncClient` and is injected into `WorkflowEngine` via constructor. It handles template rendering, environment variable resolution, retry logic, and error wrapping.

**Alternative considered**: Using the existing Tool system for HTTP calls. Rejected because Tools are in the Agent layer and workflow actions are infrastructure-level HTTP calls without LLM involvement. Mixing them would violate layer separation.

### 3. Endpoint Resolution: Step > Profile Fallback

Action step endpoints are resolved with a two-level fallback:
1. `step.endpoint` (if set)
2. `profile.workflow_action_endpoint` (profile-level default)

Headers are merged: profile defaults + step overrides (step wins on conflict).

This allows profiles to set a base API URL while individual steps can override for specific endpoints.

### 4. Full Async Engine Conversion

All public engine methods (`start`, `advance`, `resume`, `cancel`, `get_session`) converted to `async def`. This is a cascading change that requires `await` in all callers (Gateway, GraphExecutor).

**Alternative considered**: Making only `_process_current_step` async and wrapping with `asyncio.run()` in sync methods. Rejected because FastAPI endpoints are already async, and mixing sync/async creates event loop conflicts.

### 5. PostgreSQL Session Persistence (No Redis)

`WorkflowSessionStore` uses `workflow_states` table with UPSERT pattern. Sessions are saved after every state change. This replaces the in-memory `_sessions: dict`.

Follows the PostgreSQL-only infrastructure principle. The existing `workflow_states` table schema is reused with JSONB columns for flexible state storage.

### 6. Per-Workflow Escape Keywords with Global Fallback

`WorkflowDefinition.escape_keywords: list[str]` overrides the global `_ESCAPE_KEYWORDS` constant. Empty list = global fallback. This ensures workflow isolation without requiring code changes per workflow.

## Consequences

### Positive
- Zero-code deployment of new API-connected chatbots via YAML
- Session survival across server restarts
- Workflow-level escape keyword isolation
- Clean async pipeline from Gateway to external HTTP call

### Negative
- Cascading async change touched 5+ files (Gateway, GraphExecutor, bootstrap, tests)
- In-memory session fallback still exists for backward compatibility, adding code complexity
- Environment variable resolution in YAML introduces a new pattern that needs documentation

### Risks
- External API failures during action steps can leave workflows in error state (mitigated by `on_error_message` and retry logic)
- Large number of concurrent workflows could generate many PostgreSQL writes (mitigated by UPSERT deduplication)
