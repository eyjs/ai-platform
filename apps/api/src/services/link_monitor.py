"""내부 서비스 링크 감시 — KMS·DocForge 상시 연결 보장의 관측 축.

KMS ↔ ai-platform ↔ DocForge 는 공유 도커 네트워크(kms-aip-shared)로 항상
연결되어 있어야 한다(운영 원칙). 네트워크는 컴포즈가 보장하지만 "연결이
살아있는지"는 아무도 보지 않아, 상대 서비스가 죽으면 잡 실패·빈손 검색으로만
간접 발현됐다(실사고: docforge 포화 중 kms_sync 잡이 빈 에러로 무음 소진).

이 모니터는:
- 부팅 시 1회 + 주기(기본 60s)로 각 링크의 헬스 엔드포인트를 찌른다
- 상태 전이(up→down, down→up)만 WARN/INFO 로 로깅한다 (무전이 무소음)
- 최신 상태를 /api/health 응답에 노출한다 (외부에서 상시 연결 확인 가능)

실패해도 요청 경로에 개입하지 않는다 — 순수 관측(장애 시 fail-soft).
"""

from __future__ import annotations

import asyncio
import time

import httpx

from src.observability.logging import get_logger

logger = get_logger(__name__)

_PROBE_TIMEOUT = 5.0


class LinkMonitor:
    """내부 서비스 링크 상태 감시자.

    targets: {"kms": "http://kms-api:3000/api/health/live", ...}
    """

    def __init__(self, targets: dict[str, str], interval_seconds: int = 60):
        self._targets = {name: url for name, url in targets.items() if url}
        self._interval = interval_seconds
        self._status: dict[str, dict] = {
            name: {"up": None, "checked_at": None, "detail": "unchecked"}
            for name in self._targets
        }
        self._task: asyncio.Task | None = None

    @property
    def status(self) -> dict[str, dict]:
        """링크별 최신 상태 스냅샷 (health 응답용)."""
        return {name: dict(s) for name, s in self._status.items()}

    async def check_once(self) -> dict[str, dict]:
        """모든 링크를 1회 점검하고 상태 전이를 로깅한다."""
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            results = await asyncio.gather(
                *(self._probe(client, name, url) for name, url in self._targets.items()),
            )
        for name, up, detail in results:
            prev = self._status[name]["up"]
            self._status[name] = {
                "up": up,
                "checked_at": round(time.time(), 1),
                "detail": detail,
            }
            if prev is None:
                # 부팅 첫 점검: 상태를 항상 남긴다 (연결 전제의 명시적 증거)
                log = logger.info if up else logger.warning
                log("link_status", link=name, up=up, detail=detail)
            elif prev != up:
                if up:
                    logger.info("link_recovered", link=name, detail=detail)
                else:
                    logger.warning("link_down", link=name, detail=detail)
        return self.status

    @staticmethod
    async def _probe(
        client: httpx.AsyncClient, name: str, url: str,
    ) -> tuple[str, bool, str]:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return name, True, "ok"
            return name, False, f"http {resp.status_code}"
        except Exception as e:
            detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            return name, False, detail

    async def start(self) -> None:
        """부팅 1회 점검 후 주기 감시 태스크를 시작한다. interval<=0 이면 1회만."""
        await self.check_once()
        if self._interval > 0 and self._targets:
            self._task = asyncio.create_task(self._loop())
            logger.info(
                "link_monitor_started",
                links=sorted(self._targets),
                interval_seconds=self._interval,
            )

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.check_once()
            except Exception as e:  # 감시 실패가 감시를 죽이면 안 됨
                logger.warning("link_monitor_check_error", error=repr(e))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


def build_link_targets(settings) -> dict[str, str]:
    """settings 의 내부 서비스 URL 을 헬스 엔드포인트로 변환한다.

    미설정(빈 URL) 링크는 감시 대상에서 제외된다 — KMS 없이도 기동해야
    하는 graceful degradation 원칙과 정합.
    """
    targets: dict[str, str] = {}
    kms = (getattr(settings, "kms_api_url", "") or "").rstrip("/")
    if kms:
        targets["kms"] = f"{kms}/health/live"
    docforge = (getattr(settings, "docforge_url", "") or "").rstrip("/")
    if docforge:
        targets["docforge"] = f"{docforge}/v1/health"
    return targets
