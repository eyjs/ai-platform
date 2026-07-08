"""Profile YAML validation tests.

- 유효성(로드·필수키)은 seeds/profiles/*.yaml 전체를 동적으로 검증한다 —
  프로파일 추가/삭제 시 테스트가 하드코딩 목록 때문에 깨지지 않게(시드=선언적 정본).
- 품질(프롬프트 길이/섹션/존댓말)은 큐레이션된 대화형 프로파일만 대상.
  supervisor는 엔트리 분기 전용(그래프 미실행, 짧은 프롬프트)이라 품질 검사 제외.
"""

import os
from pathlib import Path

import pytest
import yaml


PROFILES_DIR = Path(__file__).parent.parent / "seeds" / "profiles"

# 시드 디렉토리 전체 (동적) — 유효성 검사 대상
ALL_PROFILE_FILES = sorted(p.name for p in PROFILES_DIR.glob("*.yaml"))

# 품질 검사 대상 (원칙/형식 섹션·존댓말 지침이 요구되는 대화형 프로파일)
QUALITY_PROFILE_FILES = [
    "insurance-qa.yaml",
    "general-chat.yaml",
]


def _load_profile(filename: str) -> dict:
    """프로필 YAML 파일을 로드한다."""
    path = PROFILES_DIR / filename
    assert path.exists(), f"Profile not found: {path}"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestProfileYamlValidity:
    """시드 디렉토리의 모든 프로필 YAML이 유효한지 검증 (동적)."""

    def test_seed_directory_not_empty(self):
        assert ALL_PROFILE_FILES, "seeds/profiles 가 비어있음"

    @pytest.mark.parametrize("filename", ALL_PROFILE_FILES)
    def test_profile_loads_successfully(self, filename):
        """프로필 YAML이 정상 로드된다."""
        profile = _load_profile(filename)
        assert profile is not None
        assert "id" in profile
        assert "name" in profile
        assert "system_prompt" in profile

    @pytest.mark.parametrize("filename", ALL_PROFILE_FILES)
    def test_profile_has_required_keys(self, filename):
        """프로필에 필수 키가 모두 존재한다."""
        profile = _load_profile(filename)
        required = ["id", "name", "domain_scopes", "mode", "tools", "system_prompt"]
        for key in required:
            assert key in profile, f"Missing key '{key}' in {filename}"


class TestMaxVectorChunks:
    """max_vector_chunks 값 검증."""

    def test_insurance_qa_max_vector_chunks_5(self):
        """P0-2: insurance-qa의 rag_search max_vector_chunks=5."""
        profile = _load_profile("insurance-qa.yaml")
        rag_tool = next(
            (t for t in profile["tools"] if t["name"] == "rag_search"),
            None,
        )
        assert rag_tool is not None
        assert rag_tool["config"]["max_vector_chunks"] == 5


class TestSystemPromptQuality:
    """system_prompt 품질 검증 (큐레이션된 대화형 프로파일)."""

    @pytest.mark.parametrize("filename", QUALITY_PROFILE_FILES)
    def test_system_prompt_minimum_length(self, filename):
        """system_prompt는 최소 200자 이상이어야 한다."""
        profile = _load_profile(filename)
        prompt = profile["system_prompt"]
        assert len(prompt) >= 200, (
            f"{filename}: system_prompt too short ({len(prompt)} chars)"
        )

    @pytest.mark.parametrize("filename", QUALITY_PROFILE_FILES)
    def test_system_prompt_has_principles_section(self, filename):
        """system_prompt에 '원칙' 섹션이 포함되어야 한다."""
        profile = _load_profile(filename)
        prompt = profile["system_prompt"]
        assert "원칙" in prompt, f"{filename}: missing '원칙' section"

    @pytest.mark.parametrize("filename", QUALITY_PROFILE_FILES)
    def test_system_prompt_has_format_section(self, filename):
        """system_prompt에 '형식' 섹션이 포함되어야 한다."""
        profile = _load_profile(filename)
        prompt = profile["system_prompt"]
        assert "형식" in prompt, f"{filename}: missing '형식' section"

    def test_insurance_qa_mentions_exemption(self):
        """insurance-qa는 면책사항 안내를 포함해야 한다."""
        profile = _load_profile("insurance-qa.yaml")
        prompt = profile["system_prompt"]
        assert "면책" in prompt

    @pytest.mark.parametrize("filename", QUALITY_PROFILE_FILES)
    def test_system_prompt_uses_polite_form(self, filename):
        """system_prompt에 존댓말 지침이 포함되어야 한다."""
        profile = _load_profile(filename)
        prompt = profile["system_prompt"]
        assert "존댓말" in prompt, f"{filename}: missing polite form guidance"
