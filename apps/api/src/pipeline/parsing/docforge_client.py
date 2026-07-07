"""DocForge API 클라이언트.

DocForge 파싱 서비스에 파일을 전송하고 마크다운 결과를 수신한다.
httpx.AsyncClient로 multipart/form-data 전송.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from src.observability.logging import get_logger

logger = get_logger(__name__)


class ParseError(Exception):
    """DocForge 호출 실패 예외."""


class ParseTimeoutError(ParseError):
    """DocForge 서버 측 파싱 타임아웃 (HTTP 408)."""


@dataclass(frozen=True)
class DocForgeResult:
    """DocForge API 응답을 ai-platform 내부 형태로 변환한 결과."""

    markdown: str
    metadata: dict
    stats: dict


class DocForgeClient:
    """DocForge 파싱 서비스 클라이언트.

    Parameters
    ----------
    base_url:
        DocForge 서비스 기본 URL (예: http://localhost:5001).
    timeout_sec:
        HTTP 요청 타임아웃 (초).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:5001",
        timeout_sec: float = 120.0,
        internal_key: str = "",
        max_wait_sec: float = 3600.0,
        poll_interval_sec: float = 2.0,
        submit_max_retries: int = 5,
        submit_retry_after_default_sec: float = 2.0,
        submit_retry_after_cap_sec: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # Per-HTTP-request timeout. With the async queue each request (submit /
        # poll) returns quickly, so this no longer bounds the whole parse.
        self._timeout = timeout_sec
        self._internal_key = internal_key
        # Total time to wait for an async parse job to finish before giving up.
        self._max_wait = max_wait_sec
        self._poll_interval = poll_interval_sec
        # --- Submit-stage backpressure / transient-retry budget ---
        # DocForge returns 503 + Retry-After (error code QUEUE_FULL) when its
        # parse queue is saturated. That is an explicit "back off and retry"
        # signal, NOT a hard failure -- treating it as ParseError (as before)
        # would kill a job that the queue would happily accept moments later and
        # turns transient saturation into a thundering herd. A transient
        # connection drop on submit ("Server disconnected") is likewise retried
        # briefly. Both share ONE bounded budget so submit can never loop
        # forever: at most ``submit_max_retries`` re-attempts, each capped wait.
        self._submit_max_retries = max(0, submit_max_retries)
        self._submit_retry_after_default = max(0.0, submit_retry_after_default_sec)
        self._submit_retry_after_cap = max(0.0, submit_retry_after_cap_sec)

    def _retry_after_seconds(self, resp: httpx.Response) -> float:
        """``Retry-After`` 헤더(초)를 파싱한다. 없거나 잘못되면 기본값.

        값은 ``submit_retry_after_cap_sec`` 로 상한을 둬 한 번의 대기가 과도하게
        길어지지 않도록 한다.
        """
        raw = resp.headers.get("Retry-After")
        wait = self._submit_retry_after_default
        if raw is not None:
            try:
                wait = float(int(str(raw).strip()))
            except (TypeError, ValueError):
                wait = self._submit_retry_after_default
        return min(max(0.0, wait), self._submit_retry_after_cap)

    async def _submit_with_backpressure(
        self,
        client: httpx.AsyncClient,
        submit_url: str,
        headers: dict,
        file_bytes: bytes,
        file_name: str,
        mime_type: str,
    ) -> str:
        """파일을 제출하고 job_id를 돌려준다.

        DocForge의 503(QUEUE_FULL) 배압과 일시 연결 끊김은 ``submit_max_retries``
        횟수 내에서 ``Retry-After`` 백오프 후 재시도한다. 그 외 비-503 4xx/5xx,
        ``success=false`` 응답은 기존과 동일하게 ``ParseError`` 로 즉시 거른다.
        재시도 예산은 유한하므로 제출이 무한 루프에 빠지지 않는다.
        """
        attempt = 0
        while True:
            try:
                resp = await client.post(
                    submit_url,
                    files={"file": (file_name, file_bytes, mime_type)},
                    headers=headers,
                )
            except httpx.RemoteProtocolError as exc:
                # 스트림 중간 연결 끊김("Server disconnected")은 docforge 가 CPU
                # 포화로 잠시 HTTP 응답을 못 한 일시적 과부하 신호다. 짧은 백오프
                # 후 제한된 횟수만 재시도한다. 예산을 소진하면 기존 계약대로
                # ParseError 로 표면화한다(호출부 httpx.HTTPError except 가 받는다).
                # 단, ConnectError(연결 거부=서버 다운/도달 불가)는 재시도하지 않고
                # 기존처럼 즉시 실패시킨다.
                if attempt >= self._submit_max_retries:
                    raise
                wait = min(self._submit_retry_after_default, self._submit_retry_after_cap)
                attempt += 1
                logger.info(
                    "docforge_submit_disconnect_retry",
                    file_name=file_name,
                    mime_type=mime_type,
                    attempt=attempt,
                    max_retries=self._submit_max_retries,
                    wait_sec=wait,
                    error=str(exc),
                )
                await asyncio.sleep(wait)
                continue

            is_json = resp.headers.get("content-type", "").startswith("application/json")
            body = resp.json() if is_json else {}
            error_info = body.get("error", {}) if isinstance(body, dict) else {}
            error_code = error_info.get("code", "")

            # 503 배압(QUEUE_FULL): 실패가 아니라 "잠시 후 재시도" 신호.
            if resp.status_code == 503 and (
                error_code == "QUEUE_FULL" or error_code == ""
            ):
                if attempt >= self._submit_max_retries:
                    raise ParseError(
                        "DocForge 파싱 큐가 가득 차 제출을 완료하지 못했습니다 "
                        f"(재시도 {self._submit_max_retries}회 소진): {file_name}"
                    )
                wait = self._retry_after_seconds(resp)
                attempt += 1
                logger.info(
                    "docforge_backpressure_wait",
                    file_name=file_name,
                    mime_type=mime_type,
                    attempt=attempt,
                    max_retries=self._submit_max_retries,
                    retry_after_sec=wait,
                    error_code=error_code or "QUEUE_FULL",
                )
                await asyncio.sleep(wait)
                continue

            # 그 외 비-202/200 응답은 기존과 동일하게 즉시 실패 처리.
            if resp.status_code not in (200, 202):
                error_msg = error_info.get("message", resp.text[:200])
                logger.error(
                    "docforge_submit_failed",
                    status=resp.status_code,
                    error_code=error_code,
                    file_name=file_name,
                    mime_type=mime_type,
                    error=error_msg,
                )
                raise ParseError(
                    f"DocForge submit returned {resp.status_code}: {error_msg}"
                )

            if not body.get("success"):
                error_msg = error_info.get("message", "unknown error")
                raise ParseError(f"DocForge submit failed: {error_msg}")
            return body["data"]["job_id"]

    async def parse(
        self,
        file_bytes: bytes,
        file_name: str,
        mime_type: str,
    ) -> DocForgeResult:
        """파일을 DocForge에 전송하여 파싱 결과를 받는다.

        Parameters
        ----------
        file_bytes:
            파일 바이너리 데이터.
        file_name:
            원본 파일명.
        mime_type:
            MIME type (예: application/pdf, text/csv).

        Returns
        -------
        DocForgeResult:
            마크다운 + 메타데이터 + 통계.

        Raises
        ------
        ParseError:
            DocForge 호출 실패 (타임아웃, 네트워크, 서버 에러).
        """
        submit_url = f"{self._base_url}/v1/parse/async"
        t0 = time.time()

        try:
            headers = {}
            if self._internal_key:
                headers["X-Internal-Key"] = self._internal_key

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # 1) 제출 — job_id 즉시 수신 (연결 유지 없음). 503(QUEUE_FULL)
                #    배압과 일시 연결 끊김은 제한된 횟수만큼 백오프 후 재시도한다.
                job_id = await self._submit_with_backpressure(
                    client, submit_url, headers,
                    file_bytes, file_name, mime_type,
                )

                # 2) 폴링 — 짧은 요청 반복, 완료/실패까지
                poll_url = f"{self._base_url}/v1/parse/async/{job_id}"
                poll_disconnects = 0
                while True:
                    if time.time() - t0 > self._max_wait:
                        raise ParseTimeoutError(
                            f"파일이 너무 크거나 복잡하여 파싱 시간이 초과되었습니다 "
                            f"({self._max_wait:.0f}s): {file_name}"
                        )
                    await asyncio.sleep(self._poll_interval)
                    try:
                        presp = await client.get(poll_url, headers=headers)
                    except httpx.RemoteProtocolError:
                        # keep-alive 레이스: gunicorn 이 닫은 유휴 연결을 재사용하다
                        # "Server disconnected" — 폴은 멱등 GET 이므로 새 연결로
                        # 즉시 재시도한다 (기존에는 잡 전체가 attempts 를 소모했다).
                        poll_disconnects += 1
                        if poll_disconnects > 5:
                            raise
                        logger.warning(
                            "docforge_poll_reconnect",
                            job_id=job_id, count=poll_disconnects,
                        )
                        continue
                    if presp.status_code == 404:
                        raise ParseError(f"DocForge 작업이 만료/소실되었습니다: {job_id}")
                    if presp.status_code != 200:
                        raise ParseError(f"DocForge poll returned {presp.status_code}")
                    pbody = presp.json()
                    data = pbody.get("data", {})
                    status = data.get("status")
                    if status in ("queued", "processing"):
                        continue
                    if status == "failed":
                        emsg = pbody.get("error", {}).get("message", "파싱 실패")
                        logger.error(
                            "docforge_parse_failed",
                            file_name=file_name, mime_type=mime_type, error=emsg,
                        )
                        raise ParseError(f"DocForge parse failed: {emsg}")
                    if status != "done":
                        raise ParseError(f"DocForge 알 수 없는 상태: {status}")
                    break

            latency_ms = (time.time() - t0) * 1000
            markdown = data.get("markdown", "")
            metadata = data.get("metadata", {})
            stats = data.get("stats", {})

            confidence = metadata.get("confidence")
            if confidence is not None and confidence < 0.6:
                logger.warning(
                    "docforge_low_confidence",
                    file_name=file_name,
                    confidence=confidence,
                    latency_ms=round(latency_ms, 1),
                )

            logger.info(
                "docforge_parse_ok",
                file_name=file_name,
                mime_type=mime_type,
                markdown_len=len(markdown),
                latency_ms=round(latency_ms, 1),
            )

            return DocForgeResult(
                markdown=markdown,
                metadata=metadata,
                stats=stats,
            )

        except httpx.TimeoutException as exc:
            latency_ms = (time.time() - t0) * 1000
            logger.error(
                "docforge_timeout",
                file_name=file_name,
                mime_type=mime_type,
                timeout_sec=self._timeout,
                latency_ms=round(latency_ms, 1),
            )
            raise ParseError(
                f"DocForge timeout after {self._timeout}s: {exc}"
            ) from exc

        except httpx.ConnectError as exc:
            logger.error(
                "docforge_connect_error",
                file_name=file_name,
                url=self._base_url,
                error=str(exc),
            )
            raise ParseError(
                f"DocForge connection failed ({self._base_url}): {exc}"
            ) from exc

        except httpx.HTTPError as exc:
            logger.error(
                "docforge_http_error",
                file_name=file_name,
                error=str(exc),
            )
            raise ParseError(f"DocForge HTTP error: {exc}") from exc

    async def health_check(self) -> bool:
        """DocForge 서비스 가용성 확인.

        Returns
        -------
        bool:
            True이면 서비스 정상.
        """
        url = f"{self._base_url}/v1/health"
        try:
            headers = {}
            if self._internal_key:
                headers["X-Internal-Key"] = self._internal_key

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                body = resp.json()
                return body.get("success", False)
            return False
        except httpx.HTTPError:
            return False
