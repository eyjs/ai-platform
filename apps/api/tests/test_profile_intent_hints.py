"""Step 23: kms-assistant intent_hints 스키마 정합 + 로더 방어 테스트.

- YAML 정합(name/patterns) 로드 성공
- 구버전 단수 pattern/intent 폴백 내성
- 필수 키 누락 시 명확한 ValueError
"""
from pathlib import Path

import pytest
import yaml

from src.agent.profile_store import ProfileStore
from src.domain.agent_profile import IntentHint


SEEDS_DIR = Path(__file__).resolve().parents[1] / "seeds" / "profiles"


def test_kms_assistant_yaml_loads_intent_hints():
    # Arrange
    raw = yaml.safe_load((SEEDS_DIR / "kms-assistant.yaml").read_text(encoding="utf-8"))

    # Act
    profile = ProfileStore._parse_profile(raw)

    # Assert
    assert profile.id == "kms-assistant"
    assert len(profile.intent_hints) == 3
    names = {h.name for h in profile.intent_hints}
    assert names == {"SEARCH", "COMPARISON", "SUMMARY"}
    for h in profile.intent_hints:
        assert isinstance(h.patterns, list)
        assert h.patterns  # 비어있지 않음


def test_canonical_schema_parsed():
    # Arrange
    h = {"name": "CLAIM", "patterns": ["청구.*해줘"], "description": "보험금 청구"}

    # Act
    hint = ProfileStore._parse_intent_hint(h, "p1", 0)

    # Assert
    assert hint == IntentHint(name="CLAIM", patterns=["청구.*해줘"], description="보험금 청구")


def test_legacy_singular_pattern_fallback():
    # Arrange: 구버전 단수 pattern + intent
    h = {"intent": "SEARCH", "pattern": "문서.*찾아"}

    # Act
    hint = ProfileStore._parse_intent_hint(h, "legacy", 0)

    # Assert: name<-intent, patterns<-[pattern]
    assert hint.name == "SEARCH"
    assert hint.patterns == ["문서.*찾아"]


def test_missing_name_raises_clear_error():
    # Arrange
    h = {"patterns": ["x"]}

    # Act / Assert
    with pytest.raises(ValueError) as exc:
        ProfileStore._parse_intent_hint(h, "broken", 2)
    msg = str(exc.value)
    assert "broken" in msg
    assert "name" in msg


def test_missing_patterns_raises_clear_error():
    # Arrange
    h = {"name": "SEARCH"}

    # Act / Assert
    with pytest.raises(ValueError) as exc:
        ProfileStore._parse_intent_hint(h, "broken", 1)
    msg = str(exc.value)
    assert "SEARCH" in msg
    assert "patterns" in msg


def test_string_patterns_coerced_to_list():
    # Arrange: patterns가 문자열로 잘못 들어온 경우도 1-요소 리스트로 정규화
    h = {"name": "X", "patterns": "단일패턴"}

    # Act
    hint = ProfileStore._parse_intent_hint(h, "p", 0)

    # Assert
    assert hint.patterns == ["단일패턴"]
