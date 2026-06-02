"""API 키 종류 정책 — publishable/secret 분리 (B4).

브라우저(위젯)에 노출되는 publishable 키와 서버 전용 secret 키를 구분한다.
publishable 키가 유출되어도 피해를 최소화하기 위해, 발급 시점에 강한 제약을 강제하고
(validate_publishable_config) 검증 시점에 보안등급을 한 번 더 클램프한다
(clamp_security_for_publishable, defense-in-depth).

순수 함수만 둔다. DB·요청·설정 객체에 의존하지 않는다 (profile_authz.py 와 동일 스타일).
"""

from __future__ import annotations

from src.domain.models import SECURITY_HIERARCHY, SecurityLevel, UserRole

PUBLISHABLE = "publishable"
SECRET = "secret"
VALID_KEY_TYPES: frozenset[str] = frozenset({PUBLISHABLE, SECRET})


def validate_publishable_config(
    *,
    security_level_max: str,
    user_role: str,
    allowed_origins: list[str] | None,
    rate_limit_per_min: int,
    rate_limit_cap: int,
) -> str | None:
    """publishable 키 발급 설정이 제약을 만족하는지 검사한다.

    Returns:
        위반 사유 문자열(거부) 또는 None(통과).

    제약:
        1. allowed_origins 필수 — 비면 어떤 오리진에서도 무차별 사용 가능해 거부.
        2. 보안등급 ≤ PUBLIC — 위젯이 INTERNAL+ 문서에 닿으면 안 됨.
        3. 역할 = VIEWER — 쓰기/관리 스코프 금지.
        4. rate_limit ≤ 상한 — 공개 키 남용으로 인한 폭주 방지.
    """
    origins = [o for o in (allowed_origins or []) if o and o.strip()]
    if not origins:
        return "publishable 키는 allowed_origins(허용 오리진)가 최소 1개 필요합니다"

    if SECURITY_HIERARCHY.get(security_level_max, 99) > SECURITY_HIERARCHY[SecurityLevel.PUBLIC]:
        return (
            f"publishable 키의 보안등급은 PUBLIC만 허용됩니다 (요청: {security_level_max})"
        )

    if user_role != UserRole.VIEWER:
        return f"publishable 키는 VIEWER 역할만 허용됩니다 (요청: {user_role})"

    if rate_limit_per_min > rate_limit_cap:
        return (
            f"publishable 키의 분당 쿼터는 {rate_limit_cap} 이하여야 합니다 "
            f"(요청: {rate_limit_per_min})"
        )

    return None


def clamp_security_for_publishable(security_level_max: str) -> str:
    """publishable 키의 보안등급을 PUBLIC로 클램프한다 (런타임 방어).

    발급 검증을 통과한 키라도 DB 행이 어떤 경로로든 더 높은 등급을 갖게 되면
    검증 시점에 PUBLIC로 강등해 INTERNAL+ 문서 접근을 원천 차단한다.
    """
    if SECURITY_HIERARCHY.get(security_level_max, 99) > SECURITY_HIERARCHY[SecurityLevel.PUBLIC]:
        return SecurityLevel.PUBLIC
    return security_level_max
