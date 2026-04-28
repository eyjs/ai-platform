"""Saju Report 통합 테스트.

SajuReportService E2E flow: generate → status → result.
Paper 7섹션, Compatibility 6섹션. LLM은 목킹, DB는 asyncpg Pool 목킹.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.domain.agent_context import AgentContext
from src.services.saju_report_service import SajuReportService
from src.tools.base import ToolResult
from src.tools.internal.saju_prompts import COMPAT_V4_SECTION_KEYS, PAPER_V2_SECTION_KEYS


SAMPLE_SAJU_DATA = {
    "basic": {"name": "테스트", "gender": "male", "birthYear": 1990},
    "premium": {"fourPillars": []},
}

SAMPLE_COMPAT_DATA = {
    "me": {"basic": {"name": "본인", "gender": "male"}, "premium": {}},
    "partner": {"basic": {"name": "상대", "gender": "female"}, "premium": {}},
}


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


def _make_llm_provider(section_keys: list[str]) -> MagicMock:
    """각 섹션별 LLM 응답을 반환하는 mock LLMProvider."""
    llm = MagicMock()
    responses = iter(
        json.dumps({"summary": f"{key} 분석 결과", "advice": f"{key} 조언"})
        for key in section_keys
    )
    llm.generate = AsyncMock(side_effect=lambda **kwargs: next(responses))
    return llm


class TestSajuReportServicePaper:
    @pytest.fixture
    def pool(self):
        return _make_pool()

    @pytest.fixture
    def llm(self):
        return _make_llm_provider(PAPER_V2_SECTION_KEYS)

    @pytest.fixture
    def service(self, pool, llm):
        return SajuReportService(pool, llm)

    async def test_process_paper_report_success(self, service, pool):
        job_id = str(uuid.uuid4())
        payload = {
            "report_type": "paper",
            "saju_data": SAMPLE_SAJU_DATA,
            "metadata": {},
            "user_id": "user-1",
            "job_id": job_id,
        }

        result = await service.process_report_job(payload)

        assert result["status"] == "completed"
        assert result["report_type"] == "paper"
        assert result["sections_count"] == 8  # 7 sections + $schema key
        assert result["job_id"] == job_id

        insert_calls = [
            c for c in pool.execute.call_args_list
            if "INSERT INTO saju_report_results" in str(c)
        ]
        assert len(insert_calls) >= 1

    async def test_paper_generates_7_sections(self, service, llm):
        job_id = str(uuid.uuid4())
        payload = {
            "report_type": "paper",
            "saju_data": SAMPLE_SAJU_DATA,
            "metadata": {},
            "user_id": "user-1",
            "job_id": job_id,
        }

        await service.process_report_job(payload)
        assert llm.generate.call_count == 7

    async def test_paper_report_llm_failure_graceful_degradation(self, pool):
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("LLM down"))
        service = SajuReportService(pool, llm)

        job_id = str(uuid.uuid4())
        payload = {
            "report_type": "paper",
            "saju_data": SAMPLE_SAJU_DATA,
            "metadata": {},
            "user_id": "user-1",
            "job_id": job_id,
        }

        result = await service.process_report_job(payload)
        assert result["status"] == "completed"
        assert result["sections_count"] == 1  # only $schema key, 0 actual sections


class TestSajuReportServiceCompatibility:
    @pytest.fixture
    def pool(self):
        return _make_pool()

    @pytest.fixture
    def llm(self):
        return _make_llm_provider(COMPAT_V4_SECTION_KEYS)

    @pytest.fixture
    def service(self, pool, llm):
        return SajuReportService(pool, llm)

    async def test_process_compat_report_success(self, service, pool):
        job_id = str(uuid.uuid4())
        payload = {
            "report_type": "compatibility",
            "saju_data": SAMPLE_COMPAT_DATA,
            "metadata": {},
            "user_id": "user-2",
            "job_id": job_id,
        }

        result = await service.process_report_job(payload)

        assert result["status"] == "completed"
        assert result["report_type"] == "compatibility"
        assert result["sections_count"] == 7  # 6 sections + $schema key

    async def test_compat_generates_6_sections(self, service, llm):
        job_id = str(uuid.uuid4())
        payload = {
            "report_type": "compatibility",
            "saju_data": SAMPLE_COMPAT_DATA,
            "metadata": {},
            "user_id": "user-2",
            "job_id": job_id,
        }

        await service.process_report_job(payload)
        assert llm.generate.call_count == 6


class TestSajuReportServiceStatusResult:
    @pytest.fixture
    def pool(self):
        return _make_pool()

    @pytest.fixture
    def llm(self):
        return _make_llm_provider(PAPER_V2_SECTION_KEYS)

    @pytest.fixture
    def service(self, pool, llm):
        return SajuReportService(pool, llm)

    async def test_get_report_status_found(self, service, pool):
        now = datetime.now(timezone.utc)
        job_uuid = uuid.uuid4()
        pool.fetchrow = AsyncMock(return_value={
            "job_id": job_uuid,
            "status": "completed",
            "sections_completed": 7,
            "sections_total": 7,
            "error_message": None,
            "created_at": now,
            "completed_at": now,
        })

        result = await service.get_report_status(str(job_uuid))
        assert result is not None
        assert result["status"] == "completed"
        assert result["sections_completed"] == 7

    async def test_get_report_status_not_found(self, service, pool):
        pool.fetchrow = AsyncMock(return_value=None)
        result = await service.get_report_status(str(uuid.uuid4()))
        assert result is None

    async def test_get_report_result_completed(self, service, pool):
        job_uuid = uuid.uuid4()
        pool.fetchrow = AsyncMock(return_value={
            "job_id": job_uuid,
            "status": "completed",
            "report_type": "paper",
            "report_data": {"sajuWonguk": {"llmText": {"summary": "test"}}},
            "error_message": None,
        })

        result = await service.get_report_result(str(job_uuid))
        assert result is not None
        assert result["report_type"] == "paper"
        assert "sajuWonguk" in result["report_data"]

    async def test_get_report_result_json_string(self, service, pool):
        job_uuid = uuid.uuid4()
        pool.fetchrow = AsyncMock(return_value={
            "job_id": job_uuid,
            "status": "completed",
            "report_type": "paper",
            "report_data": '{"key": "val"}',
            "error_message": None,
        })

        result = await service.get_report_result(str(job_uuid))
        assert result["report_data"] == {"key": "val"}

    async def test_unsupported_report_type(self, service, pool):
        payload = {
            "report_type": "unknown_type",
            "saju_data": SAMPLE_SAJU_DATA,
            "metadata": {},
            "user_id": "user-1",
            "job_id": str(uuid.uuid4()),
        }

        with pytest.raises(ValueError, match="지원하지 않는 리포트 타입"):
            await service.process_report_job(payload)


class TestSajuReportPaperToolDirect:
    async def test_paper_tool_missing_saju_data(self):
        from src.tools.internal.saju_report_paper import SajuReportPaperTool

        llm = MagicMock()
        tool = SajuReportPaperTool(llm_provider=llm)
        context = AgentContext(user_id="u1", session_id="s1")

        result = await tool.execute(params={}, context=context)
        assert not result.success
        assert "saju_data" in result.error

    async def test_paper_tool_returns_7_sections(self):
        from src.tools.internal.saju_report_paper import SajuReportPaperTool

        llm = MagicMock()
        llm.generate = AsyncMock(
            return_value='{"summary": "ok", "advice": "good"}'
        )
        tool = SajuReportPaperTool(llm_provider=llm)
        context = AgentContext(user_id="u1", session_id="s1")

        result = await tool.execute(
            params={"saju_data": SAMPLE_SAJU_DATA}, context=context,
        )

        assert result.success
        assert result.metadata["sections_completed"] == 7
        assert result.metadata["sections_total"] == 7
        for key in PAPER_V2_SECTION_KEYS:
            assert key in result.data


class TestSajuReportCompatToolDirect:
    async def test_compat_tool_missing_saju_data(self):
        from src.tools.internal.saju_report_compatibility import SajuReportCompatibilityTool

        llm = MagicMock()
        tool = SajuReportCompatibilityTool(llm_provider=llm)
        context = AgentContext(user_id="u1", session_id="s1")

        result = await tool.execute(params={}, context=context)
        assert not result.success

    async def test_compat_tool_returns_6_sections(self):
        from src.tools.internal.saju_report_compatibility import SajuReportCompatibilityTool

        llm = MagicMock()
        llm.generate = AsyncMock(
            return_value='{"summary": "ok", "advice": "good"}'
        )
        tool = SajuReportCompatibilityTool(llm_provider=llm)
        context = AgentContext(user_id="u1", session_id="s1")

        result = await tool.execute(
            params={"saju_data": SAMPLE_COMPAT_DATA}, context=context,
        )

        assert result.success
        assert result.metadata["sections_completed"] == 6
        assert result.metadata["sections_total"] == 6
        for key in COMPAT_V4_SECTION_KEYS:
            assert key in result.data
