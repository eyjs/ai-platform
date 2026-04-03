"""AccessPolicyStore: Profile별 segment 접근 정책 관리.

profile_access_policies 테이블에서 정책을 로드하여 메모리 캐시로 유지한다.
정책이 없는 Profile은 전체 공개(모든 user_type 허용).
정책이 있으면 user_type이 허용 segment에 포함되어야 접근 가능.
"""

from __future__ import annotations

import asyncpg

from src.observability.logging import get_logger

logger = get_logger(__name__)


class AccessPolicyStore:
    """Profile별 segment 접근 정책 관리.

    메모리 캐시 + DB 조회. 정책이 없는 Profile은 전체 공개.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        # {profile_id: set[segment]} -- 키가 없으면 해당 profile은 전체 공개
        self._policies: dict[str, set[str]] = {}

    async def load(self) -> None:
        """DB에서 전체 정책을 로드하여 메모리 캐시에 저장한다."""
        rows = await self._pool.fetch(
            "SELECT profile_id, segment FROM profile_access_policies"
        )

        policies: dict[str, set[str]] = {}
        for row in rows:
            profile_id: str = row["profile_id"]
            segment: str = row["segment"]
            if profile_id not in policies:
                policies[profile_id] = set()
            policies[profile_id].add(segment)

        self._policies = policies
        logger.info(
            "access_policies_loaded",
            profile_count=len(policies),
            total_rules=len(rows),
        )

    def is_allowed(self, profile_id: str, user_type: str) -> bool:
        """해당 user_type이 profile_id에 접근 가능한지 판단한다.

        정책 규칙:
        1. profile_id에 대한 정책이 없으면 -> True (전체 공개)
        2. 정책이 있으면 -> user_type이 allowed segments에 포함되어야 True
        3. user_type이 빈 문자열이고 정책이 있으면 -> False
        """
        allowed = self._policies.get(profile_id)
        if allowed is None:
            return True
        if not user_type:
            return False
        return user_type in allowed

    def get_allowed_segments(self, profile_id: str) -> set[str]:
        """profile_id에 허용된 segment 목록을 반환한다.

        정책이 없으면 빈 set 반환 (전체 공개 의미).
        """
        return set(self._policies.get(profile_id, set()))

    async def reload(self) -> None:
        """정책을 DB에서 다시 로드한다. 운영 중 정책 변경 시 호출."""
        await self.load()
        logger.info("access_policies_reloaded")
