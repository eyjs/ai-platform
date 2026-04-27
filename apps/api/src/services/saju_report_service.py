"""Saju Report Service.

사주 리포트 생성 비즈니스 로직:
- QueueWorker 핸들러 (process_report_job)
- 리포트 상태/결과 조회
- saju_report_results 테이블 관리
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import asyncpg

from src.domain.agent_context import AgentContext
from src.domain.models import SearchScope
from src.infrastructure.providers.base import LLMProvider
from src.observability.logging import get_logger
from src.tools.internal.saju_report_compatibility import SajuReportCompatibilityTool
from src.tools.internal.saju_report_paper import SajuReportPaperTool

logger = get_logger(__name__)


class SajuReportService:
    """사주 리포트 생성 서비스.

    JobQueue에서 작업을 받아 saju report tools를 호출하고,
    진행 상태를 saju_report_results 테이블에 실시간 업데이트.
    """

    def __init__(self, pool: asyncpg.Pool, main_llm: LLMProvider):
        self._pool = pool
        self._main_llm = main_llm

        # Tool 인스턴스 초기화 (LLMProvider 전달)
        self._paper_tool = SajuReportPaperTool(llm_provider=main_llm)
        self._compat_tool = SajuReportCompatibilityTool(llm_provider=main_llm)

    async def process_report_job(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """QueueWorker에서 호출되는 리포트 생성 핸들러.

        JobQueue의 job dict는 {"id": job_id, "payload": {...}} 형태로 전달됨.
        실제 job_id는 별도로 전달되어야 하므로 payload에서 추출하지 말고
        QueueWorker에서 별도로 전달하도록 수정이 필요함.

        하지만 현재 구조상 payload만 받으므로, job_id를 payload에 포함시켜야 함.
        """
        report_type = payload["report_type"]
        saju_data = payload["saju_data"]
        metadata = payload.get("metadata", {})
        user_id = payload["user_id"]

        # JobQueue에서 생성된 실제 job_id 사용 (QueueWorker에서 payload에 추가)
        job_id = payload.get("job_id", str(uuid.uuid4()))

        logger.info(
            "saju_report_job_start",
            job_id=job_id,
            report_type=report_type,
            user_id=user_id,
        )

        try:
            # 1. 리포트 레코드 초기화
            sections_total = 7 if report_type == "paper" else 6
            await self._create_report_record(
                job_id=job_id,
                report_type=report_type,
                sections_total=sections_total,
                metadata=metadata,
            )

            # 2. AgentContext 구성
            agent_context = AgentContext(
                user_id=user_id,
                session_id=f"report_{job_id}",
                search_scopes=[SearchScope.ALL],  # saju report는 전역 검색
            )

            # 3. 리포트 타입별 Tool 호출
            if report_type == "paper":
                report_data = await self._generate_paper_report(
                    job_id=job_id,
                    saju_data=saju_data,
                    agent_context=agent_context,
                )
            elif report_type == "compatibility":
                report_data = await self._generate_compatibility_report(
                    job_id=job_id,
                    saju_data=saju_data,
                    agent_context=agent_context,
                )
            else:
                raise ValueError(f"지원하지 않는 리포트 타입: {report_type}")

            # 4. 완료 상태로 업데이트
            await self._update_report_status(
                job_id=job_id,
                status="completed",
                report_data=report_data,
                sections_completed=sections_total,
            )

            logger.info(
                "saju_report_job_completed",
                job_id=job_id,
                report_type=report_type,
                sections_count=len(report_data),
            )

            return {
                "job_id": job_id,
                "report_type": report_type,
                "sections_count": len(report_data),
                "status": "completed",
            }

        except Exception as e:
            # 실패 상태로 업데이트
            await self._update_report_status(
                job_id=job_id,
                status="failed",
                error_message=str(e),
            )
            logger.error(
                "saju_report_job_failed",
                job_id=job_id,
                report_type=report_type,
                error=str(e),
                exc_info=True,
            )
            raise

    async def _generate_paper_report(
        self,
        job_id: str,
        saju_data: Dict[str, Any],
        agent_context: AgentContext,
    ) -> Dict[str, Any]:
        """Paper 리포트 생성 (7섹션)."""

        # Tool 파라미터 구성 (execute 메서드 사용)
        params = {"saju_data": saju_data}

        # SajuReportPaperTool 호출
        result = await self._paper_tool.execute(
            params=params,
            context=agent_context,
        )

        if not result.success:
            raise RuntimeError(f"Paper 리포트 생성 실패: {result.error}")

        # 진행 상태를 완료로 업데이트 (tool에서 progress 콜백이 없으므로)
        await self._update_progress(job_id, 7, 7)

        return result.data

    async def _generate_compatibility_report(
        self,
        job_id: str,
        saju_data: Dict[str, Any],
        agent_context: AgentContext,
    ) -> Dict[str, Any]:
        """Compatibility 리포트 생성 (6섹션)."""

        # Tool 파라미터 구성 (execute 메서드 사용)
        params = {"saju_data": saju_data}

        # SajuReportCompatibilityTool 호출
        result = await self._compat_tool.execute(
            params=params,
            context=agent_context,
        )

        if not result.success:
            raise RuntimeError(f"Compatibility 리포트 생성 실패: {result.error}")

        # 진행 상태를 완료로 업데이트 (tool에서 progress 콜백이 없으므로)
        await self._update_progress(job_id, 6, 6)

        return result.data

    async def _update_progress(self, job_id: str, sections_completed: int, sections_total: int) -> None:
        """진행 상태 업데이트."""
        await self._update_report_status(
            job_id=job_id,
            status="generating",
            sections_completed=sections_completed,
        )
        logger.debug(
            "saju_report_progress",
            job_id=job_id,
            progress=f"{sections_completed}/{sections_total}",
        )

    async def _create_report_record(
        self,
        job_id: str,
        report_type: str,
        sections_total: int,
        metadata: Dict[str, Any],
    ) -> None:
        """리포트 레코드 초기 생성."""
        await self._pool.execute(
            """
            INSERT INTO saju_report_results
            (id, job_id, report_type, sections_total, status, metadata)
            VALUES ($1, $2, $3, $4, 'generating', $5)
            """,
            uuid.UUID(job_id),
            uuid.UUID(job_id),  # job_id를 report id로도 사용
            report_type,
            sections_total,
            json.dumps(metadata, ensure_ascii=False),
        )

    async def _update_report_status(
        self,
        job_id: str,
        status: str,
        report_data: Optional[Dict[str, Any]] = None,
        sections_completed: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """리포트 상태 업데이트."""
        set_clauses = ["status = $2"]
        params = [uuid.UUID(job_id), status]
        param_idx = 3

        if sections_completed is not None:
            set_clauses.append(f"sections_completed = ${param_idx}")
            params.append(sections_completed)
            param_idx += 1

        if report_data is not None:
            set_clauses.append(f"report_data = ${param_idx}")
            params.append(json.dumps(report_data, ensure_ascii=False))
            param_idx += 1

        if error_message is not None:
            set_clauses.append(f"error_message = ${param_idx}")
            params.append(error_message)
            param_idx += 1

        if status == "completed":
            set_clauses.append("completed_at = NOW()")

        query = f"""
            UPDATE saju_report_results
            SET {', '.join(set_clauses)}
            WHERE job_id = $1
        """

        await self._pool.execute(query, *params)

    async def get_report_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """리포트 상태 조회."""
        row = await self._pool.fetchrow(
            """
            SELECT job_id, status, sections_completed, sections_total,
                   error_message, created_at, completed_at
            FROM saju_report_results
            WHERE job_id = $1
            """,
            uuid.UUID(job_id),
        )

        if not row:
            return None

        return {
            "job_id": str(row["job_id"]),
            "status": row["status"],
            "sections_completed": row["sections_completed"],
            "sections_total": row["sections_total"],
            "error_message": row["error_message"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        }

    async def get_report_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        """완성된 리포트 데이터 조회."""
        row = await self._pool.fetchrow(
            """
            SELECT job_id, status, report_type, report_data, error_message
            FROM saju_report_results
            WHERE job_id = $1
            """,
            uuid.UUID(job_id),
        )

        if not row:
            return None

        # report_data는 JSONB로 저장됨
        report_data = row["report_data"]
        if isinstance(report_data, str):
            report_data = json.loads(report_data)

        return {
            "job_id": str(row["job_id"]),
            "status": row["status"],
            "report_type": row["report_type"],
            "report_data": report_data or {},
            "error_message": row["error_message"],
        }