"""Profile YAML validation tests.

P0-2: max_vector_chunks=5 for insurance profiles
P1-4: Enhanced system_prompt quality
"""

import os
from pathlib import Path

import pytest
import yaml


PROFILES_DIR = Path(__file__).parent.parent / "seeds" / "profiles"


def _load_profile(filename: str) -> dict:
    """프로필 YAML 파일을 로드한다."""
    path = PROFILES_DIR / filename
    assert path.exists(), f"Profile not found: {path}"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestProfileYamlValidity:
    """모든 대상 프로필 YAML이 유효한지 검증."""

    @pytest.mark.parametrize("filename", [
        "insurance-qa.yaml",
        "insurance-contract.yaml",
        "legal-contract.yaml",
        "general-chat.yaml",
    ])
    def test_profile_loads_successfully(self, filename):
        """프로필 YAML이 정상 로드된다."""
        profile = _load_profile(filename)
        assert profile is not None
        assert "id" in profile
        assert "name" in profile
        assert "system_prompt" in profile

    @pytest.mark.parametrize("filename", [
        "insurance-qa.yaml",
        "insurance-contract.yaml",
        "legal-contract.yaml",
        "general-chat.yaml",
    ])
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

    def test_insurance_contract_max_vector_chunks_5(self):
        """P0-2: insurance-contract의 rag_search max_vector_chunks=5."""
        profile = _load_profile("insurance-contract.yaml")
        rag_tool = next(
            (t for t in profile["tools"] if t["name"] == "rag_search"),
            None,
        )
        assert rag_tool is not None
        assert rag_tool["config"]["max_vector_chunks"] == 5

    def test_legal_contract_max_vector_chunks_5(self):
        """legal-contract의 rag_search max_vector_chunks=5 (기존 유지)."""
        profile = _load_profile("legal-contract.yaml")
        rag_tool = next(
            (t for t in profile["tools"] if t["name"] == "rag_search"),
            None,
        )
        assert rag_tool is not None
        assert rag_tool["config"]["max_vector_chunks"] == 5


class TestSystemPromptQuality:
    """system_prompt 품질 검증."""

    @pytest.mark.parametrize("filename", [
        "insurance-qa.yaml",
        "insurance-contract.yaml",
        "legal-contract.yaml",
        "general-chat.yaml",
    ])
    def test_system_prompt_minimum_length(self, filename):
        """system_prompt는 최소 200자 이상이어야 한다."""
        profile = _load_profile(filename)
        prompt = profile["system_prompt"]
        assert len(prompt) >= 200, (
            f"{filename}: system_prompt too short ({len(prompt)} chars)"
        )

    @pytest.mark.parametrize("filename", [
        "insurance-qa.yaml",
        "insurance-contract.yaml",
        "legal-contract.yaml",
        "general-chat.yaml",
    ])
    def test_system_prompt_has_principles_section(self, filename):
        """system_prompt에 '원칙' 섹션이 포함되어야 한다."""
        profile = _load_profile(filename)
        prompt = profile["system_prompt"]
        assert "원칙" in prompt, f"{filename}: missing '원칙' section"

    @pytest.mark.parametrize("filename", [
        "insurance-qa.yaml",
        "insurance-contract.yaml",
        "legal-contract.yaml",
        "general-chat.yaml",
    ])
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

    def test_legal_contract_mentions_expert(self):
        """legal-contract는 법률 전문가 권고를 포함해야 한다."""
        profile = _load_profile("legal-contract.yaml")
        prompt = profile["system_prompt"]
        assert "법률 전문가" in prompt

    def test_insurance_contract_mentions_consultant(self):
        """insurance-contract는 상담사 권고를 포함해야 한다."""
        profile = _load_profile("insurance-contract.yaml")
        prompt = profile["system_prompt"]
        assert "상담사" in prompt

    @pytest.mark.parametrize("filename", [
        "insurance-qa.yaml",
        "insurance-contract.yaml",
        "legal-contract.yaml",
        "general-chat.yaml",
    ])
    def test_system_prompt_uses_polite_form(self, filename):
        """system_prompt에 존댓말 지침이 포함되어야 한다."""
        profile = _load_profile(filename)
        prompt = profile["system_prompt"]
        assert "존댓말" in prompt, f"{filename}: missing polite form guidance"
