"""Structured signal emit tests (task-301).

intent_confirm / collection / concluded の4種構造信号の検証.
in-memory WorkflowStore + WorkflowEngine を使い、DB/LLM依存なし.
"""

import pytest

from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.engine import WorkflowEngine
from src.workflow.store import WorkflowStore, _parse_step


# ── 테스트용 헬퍼 ──

def _build_store(*definitions: WorkflowDefinition) -> WorkflowStore:
    store = WorkflowStore()
    for d in definitions:
        store._cache[d.id] = d
    return store


# ── 테스트용 워크플로우 정의 ──

def _confirm_workflow(intent: str = "compat", yes_label: str = "", no_label: str = "") -> WorkflowDefinition:
    """confirm 스텝이 포함된 간단한 워크플로우."""
    return WorkflowDefinition(
        id="test_confirm",
        name="confirm 테스트",
        steps=[
            WorkflowStep(
                id="ask_name",
                type="input",
                prompt="이름을 입력하세요.",
                save_as="name",
                next="confirm_step",
            ),
            WorkflowStep(
                id="confirm_step",
                type="confirm",
                prompt="확인해주세요.",
                save_as="ok",
                intent=intent,
                confirm_yes_label=yes_label,
                confirm_no_label=no_label,
                branches={"예": "done", "아니오": "ask_name"},
            ),
            WorkflowStep(id="done", type="message", prompt="완료되었습니다."),
        ],
    )


def _collection_workflow() -> WorkflowDefinition:
    """compat 수집 4스텝(partner 타겟) 워크플로우."""
    return WorkflowDefinition(
        id="test_collection",
        name="collection 테스트",
        steps=[
            WorkflowStep(
                id="ask_partner_name",
                type="input",
                prompt="이름을 알려줘.",
                save_as="partner_name",
                collection_target="partner",
                collection_field="name",
                collection_label="이름",
                next="ask_partner_birth",
            ),
            WorkflowStep(
                id="ask_partner_birth",
                type="input",
                prompt="생년월일은?",
                save_as="partner_birth_date",
                collection_target="partner",
                collection_field="birthDate",
                collection_label="생년월일",
                next="ask_partner_time",
            ),
            WorkflowStep(
                id="ask_partner_time",
                type="input",
                prompt="태어난 시각은?",
                save_as="partner_birth_time",
                collection_target="partner",
                collection_field="birthTime",
                collection_label="태어난 시각",
                next="ask_partner_gender",
            ),
            WorkflowStep(
                id="ask_partner_gender",
                type="select",
                prompt="성별은?",
                save_as="partner_gender_label",
                options=["남성", "여성"],
                collection_target="partner",
                collection_field="gender",
                collection_label="성별",
                branches={"남성": "done", "여성": "done"},
            ),
            WorkflowStep(id="done", type="message", prompt="완료."),
        ],
    )


def _terminal_message_workflow() -> WorkflowDefinition:
    """말단 message 스텝(completed)만 있는 워크플로우."""
    return WorkflowDefinition(
        id="test_terminal",
        name="terminal 테스트",
        steps=[
            WorkflowStep(id="only_step", type="message", prompt="끝입니다."),
        ],
    )


def _non_collection_workflow() -> WorkflowDefinition:
    """collection_field 없는 select 스텝 워크플로우."""
    return WorkflowDefinition(
        id="test_no_collection",
        name="비수집 테스트",
        steps=[
            WorkflowStep(
                id="select_step",
                type="select",
                prompt="선택하세요.",
                save_as="choice",
                options=["A", "B"],
                branches={"A": "done", "B": "done"},
            ),
            WorkflowStep(id="done", type="message", prompt="완료."),
        ],
    )


# ── confirm intent_confirm 테스트 ──

class TestConfirmIntentConfirm:

    async def test_confirm_intent_confirm_custom_labels(self):
        """Arrange: confirm 스텝에 intent/yes_label/no_label 지정.
        Act: 해당 스텝에 도달.
        Assert: StepResult.intent_confirm 가 지정값으로 채워짐.
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_confirm_workflow(
            intent="compat", yes_label="응", no_label="아니",
        )))
        await engine.start("test_confirm", "s1")

        # Act
        result = await engine.advance("s1", "홍길동")  # input → confirm

        # Assert
        assert result.step_type == "confirm"
        assert result.intent_confirm == {
            "intent": "compat",
            "yes_label": "응",
            "no_label": "아니",
        }

    async def test_confirm_default_labels_when_not_specified(self):
        """Arrange: confirm 스텝에 yes/no 라벨 미지정.
        Act: 해당 스텝에 도달.
        Assert: 기본값 "응"/"아니" 사용.
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_confirm_workflow(
            intent="compat", yes_label="", no_label="",
        )))
        await engine.start("test_confirm", "s1")

        # Act
        result = await engine.advance("s1", "홍길동")

        # Assert
        assert result.intent_confirm["yes_label"] == "응"
        assert result.intent_confirm["no_label"] == "아니"

    async def test_non_confirm_step_has_empty_intent_confirm(self):
        """Arrange: input/select 스텝.
        Act: 스텝 도달.
        Assert: intent_confirm == {} (빈 dict).
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_collection_workflow()))

        # Act
        result = await engine.start("test_collection", "s1")

        # Assert
        assert result.step_type == "input"
        assert result.intent_confirm == {}


# ── collection 빌드 테스트 ──

class TestCollectionBuild:

    async def test_collection_input_step_first_field_pending(self):
        """Arrange: collection 4스텝 워크플로우 시작(아무것도 수집 안 됨).
        Act: 첫 스텝(ask_partner_name) 도달.
        Assert: collection.target=="partner", 모든 필드 status=="pending", parse_preview is None.
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_collection_workflow()))

        # Act
        result = await engine.start("test_collection", "s1")

        # Assert
        assert result.step_id == "ask_partner_name"
        c = result.collection
        assert c["target"] == "partner"
        assert c["parse_preview"] is None
        assert len(c["fields"]) == 4

        # 첫 스텝에서 아무것도 수집 안 됨 → 전부 pending
        for f in c["fields"]:
            assert f["status"] == "pending", f"필드 {f['key']} status 예상 pending, 실제 {f['status']}"
            assert f["value"] is None

    async def test_collection_filled_status_after_advance(self):
        """Arrange: 이름 입력 후 두 번째 스텝(ask_partner_birth).
        Act: advance("홍길동") → ask_partner_birth 도달.
        Assert: name 필드 status=="filled", value=="홍길동"; 나머지는 pending.
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_collection_workflow()))
        await engine.start("test_collection", "s1")

        # Act: advance → ask_partner_birth
        result = await engine.advance("s1", "홍길동")

        # Assert
        c = result.collection
        assert c["target"] == "partner"
        fields_by_key = {f["key"]: f for f in c["fields"]}

        assert fields_by_key["name"]["status"] == "filled"
        assert fields_by_key["name"]["value"] == "홍길동"
        assert fields_by_key["name"]["label"] == "이름"

        assert fields_by_key["birthDate"]["status"] == "pending"
        assert fields_by_key["birthDate"]["value"] is None

        assert fields_by_key["birthTime"]["status"] == "pending"
        assert fields_by_key["gender"]["status"] == "pending"

    async def test_collection_parse_preview_is_none(self):
        """parse_preview는 now-path 골격(키 경로만 존재, 값은 None)."""
        # Arrange
        engine = WorkflowEngine(_build_store(_collection_workflow()))

        # Act
        result = await engine.start("test_collection", "s1")

        # Assert
        assert "parse_preview" in result.collection
        assert result.collection["parse_preview"] is None

    async def test_collection_select_gender_step_emits_collection(self):
        """Arrange: select 스텝(ask_partner_gender)도 collection_field 있으면 collection emit.
        Act: 이름/생년월일/시각 입력 후 gender 선택 스텝 도달.
        Assert: collection 비어있지 않음, target=="partner", gender 필드 포함.
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_collection_workflow()))
        await engine.start("test_collection", "s1")
        await engine.advance("s1", "홍길동")      # name → birthDate 스텝
        await engine.advance("s1", "1998-08-15")  # birthDate → birthTime 스텝
        result = await engine.advance("s1", "18:30")  # birthTime → gender 스텝

        # Assert
        assert result.step_id == "ask_partner_gender"
        assert result.step_type == "select"
        c = result.collection
        assert c != {}
        assert c["target"] == "partner"
        fields_by_key = {f["key"]: f for f in c["fields"]}
        assert "gender" in fields_by_key
        assert fields_by_key["gender"]["label"] == "성별"
        assert fields_by_key["gender"]["status"] == "pending"  # 아직 선택 전

        # 이미 입력된 필드들은 filled
        assert fields_by_key["name"]["status"] == "filled"
        assert fields_by_key["birthDate"]["status"] == "filled"
        assert fields_by_key["birthTime"]["status"] == "filled"

    async def test_collection_absent_for_non_collection_step(self):
        """Arrange: collection_field 없는 select 스텝.
        Act: 스텝 도달.
        Assert: collection == {} (봉투에서 None emit).
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_non_collection_workflow()))

        # Act
        result = await engine.start("test_no_collection", "s1")

        # Assert
        assert result.collection == {}


# ── concluded 테스트 ──

class TestConcluded:

    async def test_concluded_on_terminal_message_step(self):
        """Arrange: 말단 message 스텝(next 없음).
        Act: 워크플로우 시작(=말단에 바로 도달).
        Assert: completed==True and concluded==True.
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_terminal_message_workflow()))

        # Act
        result = await engine.start("test_terminal", "s1")

        # Assert
        assert result.completed is True
        assert result.concluded is True

    async def test_concluded_on_escape(self):
        """Arrange: 진행 중 이탈(나가기).
        Act: advance("나가기").
        Assert: escaped==True and concluded==True.
        """
        # Arrange
        engine = WorkflowEngine(_build_store(_collection_workflow()))
        await engine.start("test_collection", "s1")

        # Act
        result = await engine.advance("s1", "나가기")

        # Assert
        assert result.escaped is True
        assert result.concluded is True

    async def test_in_progress_step_not_concluded(self):
        """진행 중(완료 전) 스텝은 concluded==False."""
        # Arrange
        engine = WorkflowEngine(_build_store(_collection_workflow()))

        # Act
        result = await engine.start("test_collection", "s1")

        # Assert
        assert result.completed is False
        assert result.concluded is False


# ── store carry 테스트 ──

class TestStoreCarry:

    def test_parse_step_carries_collection_meta(self):
        """Arrange: yaml dict에 collection 메타 포함.
        Act: _parse_step 호출.
        Assert: WorkflowStep.collection_field / collection_label / collection_target 올바름.
        """
        # Arrange
        data = {
            "id": "ask_partner_name",
            "type": "input",
            "prompt": "이름?",
            "save_as": "partner_name",
            "collection_target": "partner",
            "collection_field": "name",
            "collection_label": "이름",
        }

        # Act
        step = _parse_step(data)

        # Assert
        assert step.collection_field == "name"
        assert step.collection_label == "이름"
        assert step.collection_target == "partner"

    def test_parse_step_carries_intent_meta(self):
        """Arrange: yaml dict에 confirm intent 메타 포함.
        Act: _parse_step 호출.
        Assert: WorkflowStep.intent / confirm_yes_label / confirm_no_label 올바름.
        """
        # Arrange
        data = {
            "id": "confirm_step",
            "type": "confirm",
            "prompt": "확인?",
            "intent": "compat",
            "confirm_yes_label": "응",
            "confirm_no_label": "아니",
        }

        # Act
        step = _parse_step(data)

        # Assert
        assert step.intent == "compat"
        assert step.confirm_yes_label == "응"
        assert step.confirm_no_label == "아니"

    def test_parse_step_defaults_empty_string_for_missing_meta(self):
        """Arrange: collection/intent 메타 없는 yaml dict.
        Act: _parse_step 호출.
        Assert: 신규 필드 모두 "" (빈 문자열 기본값).
        """
        # Arrange
        data = {"id": "simple", "type": "message", "prompt": "안녕."}

        # Act
        step = _parse_step(data)

        # Assert
        assert step.intent == ""
        assert step.confirm_yes_label == ""
        assert step.confirm_no_label == ""
        assert step.collection_target == ""
        assert step.collection_field == ""
        assert step.collection_label == ""
