"""로케일 번들 로더.

startup 시 YAML에서 1회 로드, 이후 읽기 전용 싱글턴으로 사용.
모든 regex 패턴은 로드 시점에 pre-compile하여 런타임 비용 제로.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_bundle: LocaleBundle | None = None


def get_locale() -> LocaleBundle:
    """현재 로케일 번들을 반환한다. startup 전이면 에러."""
    if _bundle is None:
        raise RuntimeError("LocaleBundle이 초기화되지 않았습니다. bootstrap에서 set_locale()을 호출하세요.")
    return _bundle


def set_locale(bundle: LocaleBundle) -> None:
    """모듈 레벨 싱글턴을 설정한다. bootstrap에서 1회 호출."""
    global _bundle
    _bundle = bundle


@dataclass(frozen=True)
class LocaleBundle:
    """로케일 번들. 언어별 문자열, 패턴, PII 규칙을 보유한다."""

    _data: dict = field(repr=False)

    # pre-compiled caches (frozen이지만 dict 내부는 mutable — __post_init__에서 채움)
    _compiled_cache: dict = field(default_factory=dict, repr=False)
    _pii_cache: list = field(default_factory=list, repr=False)
    _pii_guard_cache: list = field(default_factory=list, repr=False)
    _validator_cache: dict = field(default_factory=dict, repr=False)
    _number_cache: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        """로드 시 regex pre-compile."""
        # patterns 섹션의 리스트 패턴 compile
        patterns = self._data.get("patterns", {})
        for key, value in patterns.items():
            if isinstance(value, list) and value and isinstance(value[0], str):
                self._compiled_cache[key] = [re.compile(p) for p in value]
            elif isinstance(value, str):
                self._compiled_cache[key] = re.compile(value, re.IGNORECASE)

        # pronoun 패턴 (dict list)
        pronoun_list = patterns.get("pronoun", [])
        if pronoun_list and isinstance(pronoun_list[0], dict):
            self._compiled_cache["pronoun"] = [
                (re.compile(p["pattern"]), p.get("confidence", 0.8))
                for p in pronoun_list
            ]

        # PII 패턴
        for entry in self._data.get("pii_patterns", []):
            self._pii_cache.append(
                (re.compile(entry["pattern"]), entry["label"]),
            )

        # PII result guard
        for entry in self._data.get("pii_result_guard", []):
            self._pii_guard_cache.append(
                (re.compile(entry["pattern"]), entry["replacement"]),
            )

        # Validators
        for name, pattern in self._data.get("validators", {}).items():
            self._validator_cache[name] = re.compile(pattern)

        # Number patterns
        for pattern in self._data.get("number_patterns", []):
            self._number_cache.append(re.compile(pattern))

    # --- 문자열 접근자 ---

    def prompt(self, key: str, **kwargs: Any) -> str:
        """프롬프트 템플릿을 반환한다. kwargs가 있으면 format."""
        value = self._data.get("prompts", {}).get(key, "")
        if kwargs:
            return value.format(**kwargs)
        return value

    def message(self, key: str, **kwargs: Any) -> str:
        """메시지 템플릿을 반환하고 format한다."""
        value = self._data.get("messages", {}).get(key, "")
        if kwargs:
            return value.format(**kwargs)
        return value

    def label(self, key: str) -> str:
        """라벨을 반환한다."""
        return self._data.get("labels", {}).get(key, "")

    # --- 패턴 접근자 ---

    def raw_patterns(self, key: str) -> list[str]:
        """패턴 문자열 리스트를 반환한다."""
        value = self._data.get("patterns", {}).get(key, [])
        if isinstance(value, list):
            return value
        return [value] if value else []

    def compiled_patterns(self, key: str) -> list[re.Pattern]:
        """pre-compiled 패턴 리스트를 반환한다."""
        cached = self._compiled_cache.get(key, [])
        if isinstance(cached, list):
            return cached
        return [cached]

    def compiled_pattern(self, key: str) -> re.Pattern | None:
        """단일 pre-compiled 패턴을 반환한다."""
        cached = self._compiled_cache.get(key)
        if isinstance(cached, re.Pattern):
            return cached
        if isinstance(cached, list) and cached:
            return cached[0]
        return None

    def pronoun_patterns(self) -> list[tuple[re.Pattern, float]]:
        """대명사 패턴 [(compiled_regex, confidence), ...]을 반환한다."""
        return self._compiled_cache.get("pronoun", [])

    def pattern_set(self, key: str) -> set[str]:
        """패턴을 set으로 반환한다 (escape_keywords 등)."""
        value = self._data.get("patterns", {}).get(key, [])
        if isinstance(value, list):
            return set(value)
        return set()

    # --- 특화 접근자 ---

    @property
    def pii_patterns(self) -> list[tuple[re.Pattern, str]]:
        """PII 감지 패턴 [(compiled_regex, label), ...]."""
        return self._pii_cache

    @property
    def pii_result_guard(self) -> list[tuple[re.Pattern, str]]:
        """PII 마스킹 패턴 [(compiled_regex, replacement), ...]."""
        return self._pii_guard_cache

    @property
    def validators(self) -> dict[str, re.Pattern]:
        """입력 검증 패턴 {name: compiled_regex}."""
        return self._validator_cache

    @property
    def number_patterns(self) -> list[re.Pattern]:
        """숫자 추출 패턴 [compiled_regex, ...]."""
        return self._number_cache

    @property
    def citation_extensions(self) -> list[str]:
        """인용 파일 확장자 리스트."""
        return self._data.get("citation_extensions", ["pdf", "md", "docx"])

    def validation_hint(self, validator_name: str) -> str:
        """검증 실패 안내 메시지."""
        return self._data.get("validation_hints", {}).get(validator_name, "")

    @property
    def key_count(self) -> int:
        """로드된 키 수 (로깅용)."""
        count = 0
        for section in ("prompts", "messages", "labels", "patterns"):
            count += len(self._data.get(section, {}))
        return count

    # --- 팩토리 ---

    @classmethod
    def load(cls, path: str) -> LocaleBundle:
        """YAML 파일에서 로케일 번들을 로드한다."""
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = Path(os.getcwd()) / resolved
        with open(resolved, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(_data=data or {})
