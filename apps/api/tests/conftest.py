"""테스트 공통 fixture."""

import pytest

from src.locale.bundle import LocaleBundle, set_locale


@pytest.fixture(autouse=True, scope="session")
def _init_locale():
    """테스트 세션 시작 시 로케일 번들을 초기화한다."""
    bundle = LocaleBundle.load("src/locale/ko.yaml")
    set_locale(bundle)
