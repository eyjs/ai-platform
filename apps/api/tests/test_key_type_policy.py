"""key_type_policy 순수 정책 단위 테스트 (B4)."""

from src.domain.key_type_policy import (
    PUBLISHABLE,
    SECRET,
    VALID_KEY_TYPES,
    clamp_security_for_publishable,
    validate_publishable_config,
)


def _cfg(**overrides):
    base = dict(
        security_level_max="PUBLIC",
        user_role="VIEWER",
        allowed_origins=["https://shop.com"],
        rate_limit_per_min=60,
        rate_limit_cap=120,
    )
    base.update(overrides)
    return base


# --- validate_publishable_config ---


def test_valid_config_returns_none():
    assert validate_publishable_config(**_cfg()) is None


def test_empty_origins_rejected():
    assert validate_publishable_config(**_cfg(allowed_origins=[])) is not None
    assert validate_publishable_config(**_cfg(allowed_origins=None)) is not None
    # 공백만 있는 항목도 빈 것으로 간주
    assert validate_publishable_config(**_cfg(allowed_origins=["", "  "])) is not None


def test_internal_security_rejected():
    assert validate_publishable_config(**_cfg(security_level_max="INTERNAL")) is not None


def test_confidential_security_rejected():
    assert validate_publishable_config(**_cfg(security_level_max="CONFIDENTIAL")) is not None


def test_write_role_rejected():
    for role in ("EDITOR", "REVIEWER", "APPROVER", "ADMIN"):
        assert validate_publishable_config(**_cfg(user_role=role)) is not None


def test_rate_limit_over_cap_rejected():
    assert validate_publishable_config(**_cfg(rate_limit_per_min=121)) is not None


def test_rate_limit_at_cap_allowed():
    assert validate_publishable_config(**_cfg(rate_limit_per_min=120)) is None


# --- clamp_security_for_publishable ---


def test_clamp_lowers_internal_to_public():
    assert clamp_security_for_publishable("INTERNAL") == "PUBLIC"
    assert clamp_security_for_publishable("CONFIDENTIAL") == "PUBLIC"
    assert clamp_security_for_publishable("SECRET") == "PUBLIC"


def test_clamp_keeps_public():
    assert clamp_security_for_publishable("PUBLIC") == "PUBLIC"


def test_clamp_unknown_value_lowers_to_public():
    # 미지의 등급은 보수적으로 PUBLIC
    assert clamp_security_for_publishable("WEIRD") == "PUBLIC"


# --- 상수 ---


def test_key_type_constants():
    assert PUBLISHABLE == "publishable"
    assert SECRET == "secret"
    assert VALID_KEY_TYPES == frozenset({"publishable", "secret"})
