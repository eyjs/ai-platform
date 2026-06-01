"""domain.profile_authz — fail-closed 프로필 인가 정책 단위 테스트 (A1)."""

from src.domain.profile_authz import is_profile_allowed, resolve_allowed_profiles


# --- resolve_allowed_profiles ---


def test_empty_non_strict_returns_none_allow_all():
    # [레거시] strict=False + 빈 목록 = 필터 없음(전체 허용)
    assert resolve_allowed_profiles([], strict=False) is None
    assert resolve_allowed_profiles(None, strict=False) is None


def test_empty_strict_returns_empty_set_deny_all():
    # [핵심] strict=True + 빈/미설정 = 빈 집합(전체 거부)
    assert resolve_allowed_profiles([], strict=True) == set()
    assert resolve_allowed_profiles(None, strict=True) == set()


def test_explicit_list_returns_set_both_modes():
    assert resolve_allowed_profiles(["a", "b"], strict=True) == {"a", "b"}
    assert resolve_allowed_profiles(["a", "b"], strict=False) == {"a", "b"}


def test_wildcard_returns_none_allow_all_even_strict():
    # 와일드카드 = 의도적 전체 허용 (미설정과 구분)
    assert resolve_allowed_profiles(["*"], strict=True) is None
    assert resolve_allowed_profiles(["*", "a"], strict=True) is None


def test_blank_entries_ignored():
    assert resolve_allowed_profiles(["", "  ".strip()], strict=True) == set()
    assert resolve_allowed_profiles(["a", ""], strict=True) == {"a"}


# --- is_profile_allowed ---


def test_is_allowed_none_means_allow_all():
    assert is_profile_allowed(None, "anything") is True


def test_is_allowed_empty_set_denies_all():
    assert is_profile_allowed(set(), "anything") is False


def test_is_allowed_membership():
    allowed = {"a", "b"}
    assert is_profile_allowed(allowed, "a") is True
    assert is_profile_allowed(allowed, "z") is False
