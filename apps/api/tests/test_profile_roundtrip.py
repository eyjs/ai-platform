"""ProfileStore 라운드트립 테스트 (P0-T).

BEFORE P0-1 fix: planning_disabled 가 _profile_to_dict 에 없어서 라운드트립 시 소실됨 (RED).
AFTER  P0-1 fix: planning_disabled 가 직렬화/파싱 모두에 배선되어 보존됨 (GREEN).

라운드트립 절차:
  1. 모든 dataclass 필드를 비기본값으로 채운 dict 생성
  2. ProfileStore.parse_profile(data) → AgentProfile
  3. ProfileStore.profile_to_dict(profile) → dict (재직렬화)
  4. ProfileStore.parse_profile(re_dict) → AgentProfile (재파싱)
  5. 원본 AgentProfile vs 재파싱 AgentProfile 필드별 비교

ALLOWLIST — 라운드트립에서 정상적으로 소실·변형되는 필드와 그 이유:
  없음: P0-1 fix 이후 모든 dataclass 필드가 보존된다.

NOTE: category_scopes 는 로더에서 직렬화/파싱되지만 vector_store에 category 컬럼/파라미터가
      없어 질의에 미적용인 채로 남는다 (NOT WIRED). 라운드트립 자체는 정상이므로 allowlist 에 포함하지 않는다.
"""

import dataclasses

import pytest

from src.agent.profile_store import ProfileStore
from src.domain.agent_profile import AgentProfile, HybridTrigger, IntentHint, ToolRef
from src.domain.models import AgentMode


# --------------------------------------------------------------------------- #
# 비기본값(non-default) 검증 데이터
# --------------------------------------------------------------------------- #

FULL_PROFILE_DATA: dict = {
    "id": "test-roundtrip",
    "name": "라운드트립 테스트 프로필",
    "description": "모든 필드 검증용",
    # 도메인 스코프
    "domain_scopes": ["insurance/ga", "insurance/direct"],
    "category_scopes": ["cat-001", "cat-002"],
    "security_level_max": "INTERNAL",
    "include_common": False,
    # 오케스트레이션 모드
    "mode": "hybrid",
    "workflow_id": "test-workflow",
    "hybrid_triggers": [
        {
            "keyword_patterns": ["계약", "가입"],
            "intent_types": ["CONTRACT"],
            "workflow_id": "contract-flow",
            "description": "계약 트리거",
        }
    ],
    # 도구
    "tools": [{"name": "rag_search", "config": {"max_vector_chunks": 5}}],
    # 응답 설정
    "system_prompt": "테스트 시스템 프롬프트",
    "response_policy": "strict",
    "guardrails": ["faithfulness", "pii_filter"],
    # LLM 설정
    "router_model": "opus",
    "main_model": "haiku",
    # 메모리
    "memory_type": "session",
    "memory_ttl_seconds": 7200,
    "memory_scopes": ["local"],
    "memory_project_id": None,
    "memory_max_turns": 20,
    "memory_retention_days": 30,
    # 에이전틱 설정
    "max_tool_calls": 10,
    "agent_timeout_seconds": 60,
    # Planner — P0-1 핵심 필드
    "planning_disabled": True,
    # 워크플로우 액션
    "workflow_action_endpoint": "https://example.com/action",
    "workflow_action_headers": {"X-Custom": "value"},
    "context_adapter": "saju",
    "cache_padding_text": "도메인 배경 텍스트",
    "empty_response_fallback": "잠시 후 다시 시도해 주세요.",
    # 커스텀 Intent
    "intent_hints": [
        {
            "name": "CONTRACT",
            "patterns": ["계약", "가입"],
            "description": "계약 요청",
        }
    ],
}

# 라운드트립 후 정상 소실되는 필드 허용 목록
# P0-1 fix 이후 현재 소실 필드 없음. 추후 추가 시 아래 형식으로 기재:
#   "field_name": "소실 이유 (한 줄)",
ROUNDTRIP_ALLOWLIST: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #

def _make_store() -> ProfileStore:
    """DB 연결 없이 ProfileStore 인스턴스 생성 (static 메서드만 사용)."""
    store = ProfileStore.__new__(ProfileStore)
    return store


def _profile_to_comparable(profile: AgentProfile) -> dict:
    """AgentProfile → 비교 가능한 기본 타입 dict."""
    result: dict = {}
    for f in dataclasses.fields(profile):
        val = getattr(profile, f.name)
        if isinstance(val, AgentMode):
            result[f.name] = val.value
        elif isinstance(val, list):
            items = []
            for item in val:
                if dataclasses.is_dataclass(item):
                    items.append(dataclasses.asdict(item))
                else:
                    items.append(item)
            result[f.name] = items
        else:
            result[f.name] = val
    return result


# --------------------------------------------------------------------------- #
# 테스트
# --------------------------------------------------------------------------- #

class TestProfileRoundtrip:
    """ProfileStore parse ↔ serialize 라운드트립 검증."""

    def test_all_fields_survive_roundtrip(self):
        """모든 dataclass 필드가 parse→serialize→reparse 후 동일해야 한다.

        BEFORE P0-1 fix: planning_disabled=True → 직렬화 누락 → 재파싱 시 False (소실, RED).
        AFTER  P0-1 fix: planning_disabled=True → 직렬화 포함 → 재파싱 시 True (보존, GREEN).
        """
        store = _make_store()

        # Step 1: 원본 파싱
        profile_original = store.parse_profile(FULL_PROFILE_DATA)

        # Step 2: 직렬화
        serialized = store.profile_to_dict(profile_original)

        # Step 3: 재파싱
        profile_reparsed = store.parse_profile(serialized)

        # Step 4: 필드별 비교
        original_dict = _profile_to_comparable(profile_original)
        reparsed_dict = _profile_to_comparable(profile_reparsed)

        dropped_fields = []
        changed_fields = []

        for field_name in original_dict:
            if field_name not in reparsed_dict:
                dropped_fields.append(field_name)
                continue
            if original_dict[field_name] != reparsed_dict[field_name]:
                if field_name not in ROUNDTRIP_ALLOWLIST:
                    changed_fields.append(
                        f"{field_name}: {original_dict[field_name]!r} → {reparsed_dict[field_name]!r}"
                    )

        # 소실 필드 중 허용목록 미포함은 실패
        unexpected_drops = [f for f in dropped_fields if f not in ROUNDTRIP_ALLOWLIST]
        assert not unexpected_drops, (
            f"라운드트립 후 비허용 필드 소실: {unexpected_drops}\n"
            f"해당 필드를 ROUNDTRIP_ALLOWLIST 에 추가하거나 로더를 수정하세요."
        )

        assert not changed_fields, (
            f"라운드트립 후 비허용 값 변경:\n" + "\n".join(f"  {c}" for c in changed_fields)
        )

    def test_planning_disabled_true_survives(self):
        """planning_disabled=True 가 라운드트립 후 보존되어야 한다 (P0-1 핵심 검증).

        P0-1 fix 이전 상태를 시뮬레이션:
          - _profile_to_dict 에 planning_disabled 가 없을 경우 재파싱 시 False 가 된다.
        P0-1 fix 이후:
          - 직렬화 dict 에 planning_disabled: True 가 포함되어 재파싱 시 True 가 된다.
        """
        store = _make_store()

        data = {"id": "p", "name": "P", "planning_disabled": True}
        profile = store.parse_profile(data)
        assert profile.planning_disabled is True, "parse_profile 이 planning_disabled=True 를 읽어야 한다"

        serialized = store.profile_to_dict(profile)
        assert serialized.get("planning_disabled") is True, (
            "profile_to_dict 가 planning_disabled=True 를 직렬화해야 한다 (P0-1 fix)"
        )

        reparsed = store.parse_profile(serialized)
        assert reparsed.planning_disabled is True, (
            "라운드트립 후 planning_disabled=True 가 보존되어야 한다"
        )

    def test_planning_disabled_false_is_default(self):
        """planning_disabled 기본값은 False 이고 라운드트립 후 유지되어야 한다."""
        store = _make_store()

        data = {"id": "p", "name": "P"}
        profile = store.parse_profile(data)
        assert profile.planning_disabled is False

        serialized = store.profile_to_dict(profile)
        assert serialized.get("planning_disabled") is False

        reparsed = store.parse_profile(serialized)
        assert reparsed.planning_disabled is False

    def test_all_dataclass_fields_in_serialized_dict(self):
        """_profile_to_dict 결과에 모든 필드(id·name·description 제외)가 존재해야 한다.

        id, name, description 은 public profile_to_dict 에서 별도 추가됨.
        나머지 필드는 _profile_to_dict 에서 직렬화되어야 한다.
        """
        store = _make_store()
        profile = store.parse_profile(FULL_PROFILE_DATA)
        serialized = store.profile_to_dict(profile)

        # public 메서드는 id, name, description 을 포함한다
        assert "id" in serialized
        assert "name" in serialized
        assert "description" in serialized

        # 나머지 모든 필드 확인
        skip_top_level = {"id", "name", "description"}
        for f in dataclasses.fields(AgentProfile):
            if f.name in skip_top_level:
                # top-level 메타 필드는 public 래퍼가 추가
                continue
            assert f.name in serialized, (
                f"필드 '{f.name}' 이 profile_to_dict 결과에 없습니다. "
                f"_profile_to_dict 에 추가해야 합니다."
            )

    def test_category_scopes_survives_roundtrip(self):
        """category_scopes 는 NOT WIRED(vector_store에 category 컬럼 없음)이지만 라운드트립 자체는 정상이어야 한다."""
        store = _make_store()
        data = {"id": "p", "name": "P", "category_scopes": ["cat-a", "cat-b"]}
        profile = store.parse_profile(data)
        assert profile.category_scopes == ["cat-a", "cat-b"]

        serialized = store.profile_to_dict(profile)
        assert serialized["category_scopes"] == ["cat-a", "cat-b"]

        reparsed = store.parse_profile(serialized)
        assert reparsed.category_scopes == ["cat-a", "cat-b"]
