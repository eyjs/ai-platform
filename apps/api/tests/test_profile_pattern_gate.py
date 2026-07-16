"""프로필 로드 시 1글자 패턴 게이트 (진단 V4).

"건" 하나가 조건·안건·건강·물건에 전부 걸려 "이 조건이 궁금해요"를 TASK로
오태깅했다. 토큰 경계 매칭으로도 못 막는다 — '조건'이 한 토큰이라 꼬리 규칙이
통하지 않는다. 그래서 로드에서 걷어낸다.

조용히 버리면 작성자가 패턴이 죽은 줄 모르므로 WARN을 남기고, 남는 패턴이 하나도
없으면(=인텐트 사멸) 에러로 세운다 — 오탐을 막으려다 인텐트를 죽이는 건 다른 사고다.
"""

import pytest

from src.agent.profile_store import ProfileStore


def test_one_char_patterns_are_dropped():
    hint = ProfileStore._parse_intent_hint(
        {"name": "TASK", "patterns": ["태스크", "작업", "건"]}, "flowsns-ops", 0,
    )
    assert hint.patterns == ["태스크", "작업"]


def test_valid_patterns_pass_through():
    hint = ProfileStore._parse_intent_hint(
        {"name": "INS", "patterns": ["보험", "보험금"]}, "insurance-qa", 0,
    )
    assert hint.patterns == ["보험", "보험금"]


def test_phrase_patterns_survive():
    hint = ProfileStore._parse_intent_hint(
        {"name": "TASK", "patterns": ["할 일", "건"]}, "flowsns-ops", 0,
    )
    assert hint.patterns == ["할 일"]


def test_all_patterns_too_short_raises():
    """전부 거부되면 인텐트가 영영 안 잡힌다 — 조용히 두면 안 된다."""
    with pytest.raises(ValueError, match="매칭될 수 없다"):
        ProfileStore._parse_intent_hint(
            {"name": "TEAM", "patterns": ["팀", "건"]}, "flowsns-ops", 0,
        )


def test_warning_is_emitted(caplog):
    with caplog.at_level("WARNING"):
        ProfileStore._parse_intent_hint(
            {"name": "TASK", "patterns": ["작업", "건"]}, "flowsns-ops", 0,
        )
    assert any("intent_hint_patterns_rejected" in r.getMessage() for r in caplog.records)


def test_shipped_profiles_have_no_dying_intents():
    """실제 시드 프로필이 게이트를 통과하는지 — 부팅이 깨지면 안 된다."""
    import pathlib

    import yaml

    for f in pathlib.Path("seeds/profiles").glob("*.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for idx, h in enumerate(data.get("intent_hints") or []):
            # 사멸 인텐트가 있으면 여기서 ValueError로 터진다
            ProfileStore._parse_intent_hint(h, data.get("id", f.stem), idx)
