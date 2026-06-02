"""Response Cache Models — contract response-cache-schema.md 에 의거."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CachedResponse:
    cache_key: str
    profile_id: str
    mode: str
    response_text: str
    prompt_tokens: int
    completion_tokens: int
    created_at: datetime
    expires_at: datetime
    hit_count: int


def normalize_input(prompt: str) -> str:
    """Contract normalize_input 규칙 구현.

    1. Unicode NFC
    2. strip
    3. 연속 whitespace → 단일 공백
    4. lowercase
    """
    normalized = unicodedata.normalize("NFC", prompt).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower()


def compute_cache_key(
    profile_id: str, mode: str, normalized_input: str, tenant_id: str | None = None,
) -> str:
    """SHA-256 hex 64자.

    tenant_id를 키에 포함해 테넌트 간 캐시 격리(A2/4b). 미포함 시 같은 profile+prompt를
    공유키 ON CONFLICT가 덮어써 타 테넌트 응답이 누설될 수 있다.
    """
    payload = f"{tenant_id or ''}|{profile_id}|{mode}|{normalized_input}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
