"""WorkflowContextAdapter 테스트.

dynamic 스텝의 도메인 enrichment가 엔진 밖(어댑터)으로 분리됐는지,
엔진은 어댑터 이름만으로 동작하며 미바인딩/실패 시 폴백하는지 검증한다.
"""

import pytest

from src.workflow.context_adapter import SajuContextAdapter, WorkflowContextAdapter
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.engine import WorkflowEngine
from src.workflow.store import WorkflowStore


# --- 테스트 더블 ---


class _EchoLLM:
    """generate가 받은 user_prompt를 그대로 돌려주는 가짜 LLM (프롬프트 주입 검증용)."""

    async def generate(self, prompt: str, system: str = "") -> str:
        return prompt


class _StubAdapter:
    """고정 블록을 반환하는 가짜 어댑터."""

    def __init__(self) -> None:
        self.calls = 0

    async def enrich(self, collected: dict) -> dict:
        self.calls += 1
        return {"stub": "\n\n[STUB-BLOCK]\n근거데이터"}


class _BoomAdapter:
    async def enrich(self, collected: dict) -> dict:
        raise RuntimeError("enrich 실패")


def _build_store(*definitions: WorkflowDefinition) -> WorkflowStore:
    store = WorkflowStore()
    for d in definitions:
        store._cache[d.id] = d
    return store


def _dynamic_workflow() -> WorkflowDefinition:
    """dynamic 한 스텝짜리 워크플로우 (entry에서 바로 통찰 생성 후 종료)."""
    return WorkflowDefinition(
        id="wf_dynamic",
        name="dynamic 테스트",
        steps=[
            WorkflowStep(
                id="insight", type="dynamic",
                system="너는 묘묘다.", prompt="통찰을 말해라.",
            ),
        ],
    )


# --- SajuContextAdapter ---


async def test_saju_adapter_no_saju_id_returns_date_only():
    """saju_id가 없어도 날짜 블록(연도 grounding)은 항상 반환한다."""
    adapter = SajuContextAdapter(backend_url="http://x:8002")
    blocks = await adapter.enrich({})
    assert set(blocks) == {"date"}
    assert "오늘 날짜" in blocks["date"]


async def test_saju_adapter_uses_cached_summaries_without_http():
    """캐시(_saju_summary/_compat_summary)가 있으면 HTTP 없이 완성 블록을 반환한다."""
    adapter = SajuContextAdapter(backend_url="http://x:8002")
    collected = {
        "saju_id": "abc",
        "_saju_summary": "중심 기운은 화, 신약",
        "compat_job": {"job": 1},
        "_compat_summary": "종합 궁합 잘 맞는 편",
    }
    blocks = await adapter.enrich(collected)
    assert set(blocks) == {"saju", "compat", "date"}
    assert "중심 기운은 화, 신약" in blocks["saju"]
    assert "종합 궁합 잘 맞는 편" in blocks["compat"]


async def test_saju_adapter_skips_compat_without_compat_job():
    """compat_job이 없으면 궁합 블록은 생략된다(HTTP 미발생)."""
    adapter = SajuContextAdapter(backend_url="http://x:8002")
    collected = {"saju_id": "abc", "_saju_summary": "중심 기운은 토"}
    blocks = await adapter.enrich(collected)
    assert set(blocks) == {"saju", "date"}


def test_saju_adapter_satisfies_protocol():
    assert isinstance(SajuContextAdapter(backend_url="http://x:8002"), WorkflowContextAdapter)


# --- 엔진 ↔ 어댑터 결합 ---


async def test_start_binds_adapter_name_to_session():
    engine = WorkflowEngine(_build_store(_dynamic_workflow()), llm=_EchoLLM())
    await engine.start("wf_dynamic", "sess-bind", context_adapter="stub")
    session = await engine.get_session("sess-bind")
    assert session.collected["_adapter"] == "stub"


async def test_dynamic_step_injects_adapter_block():
    """바인딩된 어댑터의 enrich 블록이 LLM 프롬프트(=EchoLLM 출력)에 주입된다."""
    stub = _StubAdapter()
    engine = WorkflowEngine(
        _build_store(_dynamic_workflow()), llm=_EchoLLM(),
        context_adapters={"stub": stub},
    )
    result = await engine.start("wf_dynamic", "sess-stub", context_adapter="stub")
    assert stub.calls == 1
    assert "[STUB-BLOCK]" in result.bot_message


async def test_dynamic_step_without_adapter_has_no_block():
    """어댑터 미바인딩이면 grounding 없이 진행한다(블록 없음)."""
    engine = WorkflowEngine(
        _build_store(_dynamic_workflow()), llm=_EchoLLM(),
        context_adapters={"stub": _StubAdapter()},
    )
    result = await engine.start("wf_dynamic", "sess-none")  # context_adapter 미지정
    assert "[STUB-BLOCK]" not in result.bot_message


async def test_dynamic_step_adapter_failure_falls_back():
    """enrich가 던져도 워크플로우는 진행된다(블록만 생략)."""
    engine = WorkflowEngine(
        _build_store(_dynamic_workflow()), llm=_EchoLLM(),
        context_adapters={"boom": _BoomAdapter()},
    )
    result = await engine.start("wf_dynamic", "sess-boom", context_adapter="boom")
    # LLM 폴백 없이 정상 통찰 생성(EchoLLM)되고 종료
    assert result.completed
    assert result.bot_message
