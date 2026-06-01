"""프로필 접근 허용 집합 해석 — fail-closed 정책 (A1).

멀티테넌트 격리의 첫 관문. API Key의 allowed_profiles와 테넌트의
tenant_profiles 매핑을 "필터용 집합"으로 해석한다.

strict 모드(AIP_PROFILE_AUTH_STRICT)에 따라 빈/미설정 목록의 의미가 바뀐다:
  - strict=False (레거시, 기본): 빈 목록 = 전체 허용 (fail-open)
  - strict=True (권장): 빈 목록 = 전체 거부 (deny-by-default)

명시적 전체 허용이 필요하면 와일드카드 "*"를 목록에 넣는다. 이렇게
"미설정(빈 목록)"과 "의도적 전체 허용(와일드카드)"을 구분한다.
"""

from __future__ import annotations

WILDCARD = "*"


def resolve_allowed_profiles(raw: list[str] | None, *, strict: bool) -> set[str] | None:
    """허용 프로필 목록을 필터용 집합으로 해석한다.

    Returns:
        - None     → 필터 없음(전체 허용). 와일드카드("*") 또는 (비-strict + 빈 목록).
        - set()    → 전체 거부(deny-by-default). strict + 빈/미설정 목록.
        - set(ids) → 명시된 프로필만 허용.
    """
    items = [s for s in (raw or []) if s]
    if WILDCARD in items:
        return None
    if items:
        return set(items)
    # 빈/미설정 목록
    return set() if strict else None


def is_profile_allowed(allowed: set[str] | None, profile_id: str) -> bool:
    """해석된 허용집합(resolve_allowed_profiles 반환값)에 대해 단일 프로필 접근 여부.

    None(필터 없음)이면 항상 허용, 빈 집합이면 항상 거부.
    """
    return allowed is None or profile_id in allowed
