"""RequestLog DTO — contract request-log-schema.md 에 의거."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class RequestLogEntry:
    api_key_id: Optional[str] = None
    profile_id: Optional[str] = None
    provider_id: Optional[str] = None
    status_code: int = 0
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit: bool = False
    error_code: Optional[str] = None
    request_preview: Optional[str] = None
    response_preview: Optional[str] = None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def with_(self, **changes) -> "RequestLogEntry":
        """불변 업데이트."""
        return replace(self, **changes)

    @staticmethod
    def truncate_preview(text: Optional[str], max_len: int = 200) -> Optional[str]:
        if text is None:
            return None
        return text[:max_len]
