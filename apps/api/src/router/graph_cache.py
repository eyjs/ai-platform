"""인메모리 TTL 그래프 캐시.

agentic graph의 (system_prompt, tool_names) 조합을 캐시하여
매 요청마다 발생하는 graph 재빌드를 제거한다.

- TTL 2시간, lazy eviction (접근 시점 만료 체크)
- 최대 엔트리 수 제한 (LRU 방식)
- Profile YAML 변경 시 invalidate(profile_id)로 무효화
- threading.Lock 보호 (명시적 안전)
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


DEFAULT_TTL_SECONDS = 7200  # 2시간
DEFAULT_MAX_ENTRIES = 100


@dataclass(frozen=True)
class CacheKey:
    """그래프 캐시 키. system_prompt 해시 + tool names."""

    system_prompt_hash: str
    tool_names: tuple[str, ...]


@dataclass
class CacheEntry:
    """캐시 엔트리."""

    graph: Any  # CompiledStateGraph
    created_at: float
    last_accessed: float
    profile_id: Optional[str] = None


class GraphCache:
    """인메모리 TTL 그래프 캐시.

    동일 (system_prompt, tool_names) 조합에 대해 컴파일된 그래프를 재사용한다.
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._store: dict[CacheKey, CacheEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def make_key(system_prompt: str, tool_names: list[str]) -> CacheKey:
        """캐시 키를 생성한다."""
        prompt_hash = hashlib.sha256(
            system_prompt.encode("utf-8"),
        ).hexdigest()[:16]
        return CacheKey(
            system_prompt_hash=prompt_hash,
            tool_names=tuple(sorted(tool_names)),
        )

    def get(self, key: CacheKey) -> Optional[Any]:
        """캐시에서 그래프를 조회한다. TTL 만료 시 None."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None

            now = time.time()
            if now - entry.created_at > self._ttl:
                # TTL 만료 -> lazy eviction
                del self._store[key]
                return None

            entry.last_accessed = now
            return entry.graph

    def put(
        self,
        key: CacheKey,
        graph: Any,
        profile_id: Optional[str] = None,
    ) -> None:
        """캐시에 그래프를 저장한다."""
        with self._lock:
            now = time.time()

            # max_entries 초과 시 LRU 방식으로 가장 오래된 항목 제거
            if len(self._store) >= self._max_entries and key not in self._store:
                self._evict_lru()

            self._store[key] = CacheEntry(
                graph=graph,
                created_at=now,
                last_accessed=now,
                profile_id=profile_id,
            )

    def invalidate(self, profile_id: str) -> int:
        """해당 profile_id와 연관된 모든 캐시 항목을 제거한다.

        Returns:
            제거된 항목 수
        """
        with self._lock:
            keys_to_remove = [
                k for k, v in self._store.items()
                if v.profile_id == profile_id
            ]
            for k in keys_to_remove:
                del self._store[k]
            return len(keys_to_remove)

    def invalidate_all(self) -> int:
        """모든 캐시를 제거한다.

        Returns:
            제거된 항목 수
        """
        with self._lock:
            count = len(self._store)
            self._store.clear()
            return count

    @property
    def size(self) -> int:
        """현재 캐시 엔트리 수."""
        with self._lock:
            return len(self._store)

    def _evict_lru(self) -> None:
        """가장 오래전 접근된 항목을 제거한다. Lock 내부에서만 호출."""
        if not self._store:
            return
        oldest_key = min(
            self._store,
            key=lambda k: self._store[k].last_accessed,
        )
        del self._store[oldest_key]
