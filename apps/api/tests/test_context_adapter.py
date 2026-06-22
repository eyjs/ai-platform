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
    """generate가 받은 인자를 기록하고 prompt를 그대로 돌려주는 가짜 LLM (주입 검증용).

    task-101 이후 cacheable_system/volatile_system 키워드 인자를 기록한다.
    """

    def __init__(self) -> None:
        self.last_cacheable: str = ""
        self.last_volatile: str = ""

    async def generate(
        self,
        prompt: str,
        system: str = "",
        cacheable_system: str = "",
        volatile_system: str = "",
    ) -> str:
        self.last_cacheable = cacheable_system
        self.last_volatile = volatile_system
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


class _BindBoomAdapter:
    """bind가 던지는 어댑터 — start()가 크래시 없이 graceful 진행하는지 검증."""

    def bind(self, session_id: str, collected: dict) -> None:
        raise RuntimeError("bind 실패")

    async def enrich(self, collected: dict) -> dict:
        return {}


class _BindAdapter:
    """bind hook을 제공하는 가짜 어댑터(V3 검증용). 식별자를 _hidden_keys에 등록."""

    def bind(self, session_id: str, collected: dict) -> None:
        # 도메인 규약: "svc-{id}" → collected["entity_id"] (식별자라 표시 제외 등록)
        if session_id.startswith("svc-"):
            collected["entity_id"] = session_id[len("svc-"):]
            collected.setdefault("_hidden_keys", []).append("entity_id")

    async def enrich(self, collected: dict) -> dict:
        return {}


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
    """바인딩된 어댑터의 enrich 블록이 cacheable_system 에 주입된다 (task-101).

    task-101 이후 grounding 은 user_prompt 가 아니라 cacheable_system 으로 이동했다.
    EchoLLM 이 기록한 last_cacheable 에 STUB-BLOCK 이 있으면 주입 성공.
    """
    stub = _StubAdapter()
    echo_llm = _EchoLLM()
    engine = WorkflowEngine(
        _build_store(_dynamic_workflow()), llm=echo_llm,
        context_adapters={"stub": stub},
    )
    result = await engine.start("wf_dynamic", "sess-stub", context_adapter="stub")
    assert stub.calls == 1
    # grounding 이 cacheable_system 에 포함됐는지 확인 (task-101 변경점)
    assert "[STUB-BLOCK]" in echo_llm.last_cacheable


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


# --- V3: 도메인 식별자 추출은 어댑터(bind)가 소유 ---


async def test_engine_does_not_parse_domain_id_without_adapter():
    """범용 엔진은 session_id에서 도메인 식별자를 직접 파싱하지 않는다.

    어댑터 미바인딩이면 'saju-...' 형태 세션이라도 saju_id를 주입하지 않는다.
    (엔진 도메인 무지 — 회귀 방지)
    """
    engine = WorkflowEngine(_build_store(_dynamic_workflow()), llm=_EchoLLM())
    await engine.start(
        "wf_dynamic", "saju-550e8400-e29b-41d4-a716-446655440000-discovery",
    )
    session = await engine.get_session("saju-550e8400-e29b-41d4-a716-446655440000-discovery")
    assert "saju_id" not in session.collected
    assert session.collected["session_id"].startswith("saju-")


async def test_start_calls_adapter_bind_for_domain_id():
    """엔진은 바인딩된 어댑터의 bind()를 호출해 도메인 식별자를 collected에 채운다."""
    engine = WorkflowEngine(
        _build_store(_dynamic_workflow()), llm=_EchoLLM(),
        context_adapters={"svc": _BindAdapter()},
    )
    await engine.start("wf_dynamic", "svc-ABC123", context_adapter="svc")
    session = await engine.get_session("svc-ABC123")
    assert session.collected["entity_id"] == "ABC123"


async def test_start_survives_adapter_bind_failure():
    """bind가 예외를 던져도 start()는 크래시 없이 워크플로우를 진행한다(enrich와 동일 graceful)."""
    engine = WorkflowEngine(
        _build_store(_dynamic_workflow()), llm=_EchoLLM(),
        context_adapters={"boom": _BindBoomAdapter()},
    )
    result = await engine.start("wf_dynamic", "sess-bindboom", context_adapter="boom")
    assert result.completed
    assert result.bot_message


async def test_saju_adapter_bind_extracts_uuid_from_product_session():
    """SajuContextAdapter.bind: 'saju-{uuid}-{product}' 포맷에서 UUID 추출 + hidden 등록."""
    adapter = SajuContextAdapter(backend_url="http://x:8002")
    collected: dict = {}
    adapter.bind("saju-550e8400-e29b-41d4-a716-446655440000-discovery", collected)
    assert collected["saju_id"] == "550e8400-e29b-41d4-a716-446655440000"
    # 도메인 식별자는 어댑터가 _hidden_keys에 등록 → 엔진은 도메인 키 이름을 모른다.
    assert "saju_id" in collected["_hidden_keys"]


# --- 컨텍스트 표시 필터 일반화 (엔진 도메인-무지) ---


def test_visible_ctx_lines_excludes_internal_and_hidden():
    """_visible_ctx_lines: _-prefix·session_id·어댑터 등록(_hidden_keys) 키를 제외, 나머지는 포함."""
    from src.workflow.engine import _visible_ctx_lines

    collected = {
        "session_id": "svc-1",
        "saju_id": "uuid-xyz",          # 어댑터가 hidden 등록한 식별자
        "_adapter": "saju",             # 내부 키
        "_hidden_keys": ["saju_id"],
        "topic": "연애",                 # 표시 대상
        "partner_name": "지민",          # 표시 대상
    }
    lines = _visible_ctx_lines(collected)
    joined = "\n".join(lines)
    assert "topic: 연애" in joined
    assert "partner_name: 지민" in joined
    assert "saju_id" not in joined
    assert "session_id" not in joined
    assert "_adapter" not in joined


async def test_dynamic_user_ctx_excludes_adapter_hidden_id():
    """dynamic 스텝 user_prompt에 어댑터가 hidden 등록한 식별자가 노출되지 않는다."""
    echo_llm = _EchoLLM()
    engine = WorkflowEngine(
        _build_store(_dynamic_workflow()), llm=echo_llm,
        context_adapters={"svc": _BindAdapter()},
    )
    result = await engine.start("wf_dynamic", "svc-ABC123", context_adapter="svc")
    # entity_id(=ABC123)는 식별자라 user_prompt(EchoLLM이 echo)에 들어가면 안 됨
    assert "ABC123" not in result.bot_message


# --- V2: 캐시 패딩 도메인 텍스트는 Profile(cache_padding_text)이 제공, 없으면 중립 여백 ---


async def test_padding_uses_profile_domain_text():
    """start(cache_padding_text=...)로 받은 도메인 텍스트로 cacheable_system을 채운다."""
    echo_llm = _EchoLLM()
    engine = WorkflowEngine(_build_store(_dynamic_workflow()), llm=echo_llm)
    await engine.start(
        "wf_dynamic", "sess-pad", cache_padding_text="ZZ-DOMAIN-PADDING-BLOCK ",
    )
    assert "ZZ-DOMAIN-PADDING-BLOCK" in echo_llm.last_cacheable
    assert len(echo_llm.last_cacheable) >= 16384  # 캐시 최소 크기 도달


async def test_padding_neutral_when_no_text():
    """패딩 텍스트 미지정이면 도메인 무지 중립 여백으로 패딩한다(도메인 누수 없음)."""
    echo_llm = _EchoLLM()
    engine = WorkflowEngine(_build_store(_dynamic_workflow()), llm=echo_llm)
    await engine.start("wf_dynamic", "sess-neutral")
    assert "캐시 안정용 여백" in echo_llm.last_cacheable
    assert "오행" not in echo_llm.last_cacheable  # 사주 도메인 텍스트 누수 없음
