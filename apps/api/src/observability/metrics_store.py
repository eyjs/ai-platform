"""메트릭 데이터베이스 저장소: 주기적 PostgreSQL 플러시."""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from .metrics import MetricsCollector

logger = logging.getLogger(__name__)


class MetricsStore:
    """메트릭을 PostgreSQL cache_entries 테이블에 주기적으로 저장하는 스토어."""

    def __init__(self, pool: asyncpg.Pool, collector: MetricsCollector):
        self._pool = pool
        self._collector = collector
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False

    async def flush_to_db(self, ttl_seconds: int = 3600) -> None:
        """현재 메트릭 스냅샷을 cache_entries 테이블에 저장."""
        try:
            summary = self._collector.summary()
            if not summary:
                logger.debug("No metrics to flush")
                return

            # 메트릭 키 생성 (timestamp 포함)
            timestamp = datetime.now(timezone.utc)
            metrics_key = f"metrics:snapshot:{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}"

            # expires_at 계산
            expires_at = timestamp + timedelta(seconds=ttl_seconds)

            # JSONB 포맷으로 메트릭 데이터 준비
            metrics_data = {
                "timestamp": timestamp.isoformat(),
                "metrics": summary,
                "metadata": {
                    "total_nodes": len(summary),
                    "total_calls": sum(node.get("calls", 0) for node in summary.values()),
                    "flush_ttl_seconds": ttl_seconds
                }
            }

            value_json = json.dumps(metrics_data, ensure_ascii=False)

            # cache_entries 테이블에 저장
            await self._pool.execute(
                """
                INSERT INTO cache_entries (key, value, expires_at)
                VALUES ($1, $2::jsonb, $3)
                """,
                metrics_key, value_json, expires_at
            )

            logger.info(
                "Flushed metrics snapshot: %d nodes, %d total calls -> key=%s",
                len(summary),
                sum(node.get("calls", 0) for node in summary.values()),
                metrics_key
            )

        except Exception as e:
            logger.error("Failed to flush metrics to DB: %s", e, exc_info=True)

    async def start_flusher(self, interval_seconds: int = 60) -> None:
        """주기적 메트릭 플러시 태스크를 시작."""
        if self._running:
            logger.warning("Metrics flusher is already running")
            return

        self._running = True
        self._flush_task = asyncio.create_task(
            self._flush_loop(interval_seconds)
        )
        logger.info("Started metrics flusher with %d second interval", interval_seconds)

    async def stop_flusher(self) -> None:
        """메트릭 플러시 태스크를 중지."""
        if not self._running:
            return

        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None

        logger.info("Stopped metrics flusher")

    async def _flush_loop(self, interval_seconds: int) -> None:
        """주기적 플러시 루프."""
        while self._running:
            try:
                await asyncio.sleep(interval_seconds)
                if self._running:  # 중지 신호 확인
                    await self.flush_to_db()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in metrics flush loop: %s", e, exc_info=True)

    async def get_recent_snapshots(
        self,
        limit: int = 10,
        hours_back: int = 24
    ) -> list[dict[str, Any]]:
        """최근 메트릭 스냅샷들을 조회."""
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)

        try:
            rows = await self._pool.fetch(
                """
                SELECT key, value, created_at, expires_at
                FROM cache_entries
                WHERE key LIKE 'metrics:snapshot:%'
                    AND created_at >= $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                cutoff_time, limit
            )

            snapshots = []
            for row in rows:
                try:
                    value_data = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
                    snapshots.append({
                        "key": row["key"],
                        "data": value_data,
                        "created_at": row["created_at"],
                        "expires_at": row["expires_at"]
                    })
                except json.JSONDecodeError as e:
                    logger.warning("Failed to parse metrics snapshot %s: %s", row["key"], e)

            return snapshots

        except Exception as e:
            logger.error("Failed to fetch recent metrics snapshots: %s", e, exc_info=True)
            return []

    async def cleanup_old_metrics(self, keep_days: int = 7) -> int:
        """오래된 메트릭 스냅샷을 정리."""
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=keep_days)

        try:
            result = await self._pool.execute(
                """
                DELETE FROM cache_entries
                WHERE key LIKE 'metrics:snapshot:%'
                    AND created_at < $1
                """,
                cutoff_time
            )

            count = int(result.split()[-1])
            if count > 0:
                logger.info("Cleaned up %d old metric snapshots (older than %d days)", count, keep_days)
            return count

        except Exception as e:
            logger.error("Failed to cleanup old metrics: %s", e, exc_info=True)
            return 0