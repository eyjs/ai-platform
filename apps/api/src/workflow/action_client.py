"""Workflow Action Client: 외부 HTTP API 호출 클라이언트.

action step에서 수집된 데이터를 외부 시스템에 전달한다.
httpx AsyncClient 기반. 동기 호출 금지.

사용법:
    client = ActionClient()
    result = await client.call(
        endpoint="https://api.example.com/contracts",
        method="POST",
        headers={"Authorization": "Bearer xxx"},
        payload={"name": "홍길동", "phone": "010-1234-5678"},
        timeout=30,
    )
"""

from __future__ import annotations

import os
import re

import httpx

from src.observability.logging import get_logger
from src.workflow.template import render_dict_template, render_template

logger = get_logger(__name__)

# 환경변수 참조 패턴: ${VAR_NAME}
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class WorkflowActionError(Exception):
    """외부 API 호출 실패 예외."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_body: dict | str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def _resolve_env_vars(value: str) -> str:
    """문자열 내 ${ENV_VAR} 패턴을 환경변수 값으로 치환한다.

    환경변수가 설정되지 않은 경우 빈 문자열로 치환한다.
    """
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, "")

    return _ENV_VAR_PATTERN.sub(replacer, value)


def _resolve_env_in_dict(d: dict) -> dict:
    """dict의 모든 문자열 값에서 환경변수를 치환한다."""
    result = {}
    for key, value in d.items():
        if isinstance(value, str):
            result[key] = _resolve_env_vars(value)
        elif isinstance(value, dict):
            result[key] = _resolve_env_in_dict(value)
        else:
            result[key] = value
    return result


class ActionClient:
    """외부 HTTP API 호출 클라이언트.

    - httpx.AsyncClient 사용 (sync 금지)
    - {{field}} 템플릿 렌더링
    - ${ENV_VAR} 환경변수 치환
    - HTTP 4xx/5xx -> WorkflowActionError
    - 네트워크 오류 시 1회 재시도
    """

    def __init__(self, timeout: int = 30) -> None:
        self._default_timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def call(
        self,
        endpoint: str,
        method: str = "POST",
        headers: dict | None = None,
        payload: dict | None = None,
        timeout: int | None = None,
        collected: dict | None = None,
        max_retries: int = 1,
    ) -> dict:
        """외부 API를 호출하고 응답 JSON을 반환한다.

        Args:
            endpoint: 호출할 URL ({{field}} 및 ${ENV_VAR} 치환 가능)
            method: HTTP 메서드 (GET, POST, PUT, PATCH, DELETE)
            headers: 요청 헤더 ({{field}} 및 ${ENV_VAR} 치환 가능)
            payload: 요청 바디 ({{field}} 치환 가능)
            timeout: 타임아웃 초 (None이면 기본값)
            collected: 워크플로우에서 수집된 데이터 (템플릿 렌더링용)
            max_retries: 네트워크 오류 시 재시도 횟수

        Returns:
            응답 JSON dict

        Raises:
            WorkflowActionError: HTTP 4xx/5xx 또는 네트워크 오류
        """
        data = collected or {}

        # 엔드포인트 렌더링 ({{field}} + ${ENV_VAR})
        resolved_endpoint = _resolve_env_vars(render_template(endpoint, data))

        # 헤더 렌더링
        resolved_headers = {}
        if headers:
            resolved_headers = render_dict_template(headers, data)
            resolved_headers = _resolve_env_in_dict(resolved_headers)

        # 페이로드 렌더링
        resolved_payload = None
        if payload:
            resolved_payload = render_dict_template(payload, data)

        request_timeout = timeout or self._default_timeout
        attempts = 0
        last_error: Exception | None = None

        while attempts <= max_retries:
            try:
                response = await self._client.request(
                    method=method.upper(),
                    url=resolved_endpoint,
                    headers=resolved_headers,
                    json=resolved_payload if resolved_payload else None,
                    timeout=request_timeout,
                )

                if response.status_code >= 400:
                    body = self._parse_response_body(response)
                    logger.warning(
                        "action_client_http_error",
                        endpoint=resolved_endpoint,
                        status_code=response.status_code,
                        response_body=str(body)[:200],
                    )
                    raise WorkflowActionError(
                        message=f"외부 API 호출 실패: HTTP {response.status_code}",
                        status_code=response.status_code,
                        response_body=body,
                    )

                return self._parse_response_body(response)

            except WorkflowActionError:
                # HTTP 에러는 재시도하지 않음 (4xx/5xx는 서버 응답이므로)
                raise

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                last_error = e
                attempts += 1
                if attempts <= max_retries:
                    logger.info(
                        "action_client_retry",
                        endpoint=resolved_endpoint,
                        attempt=attempts,
                        error=str(e),
                    )
                    continue

                logger.error(
                    "action_client_network_error",
                    endpoint=resolved_endpoint,
                    attempts=attempts,
                    error=str(e),
                )
                raise WorkflowActionError(
                    message=f"외부 API 연결 실패: {type(e).__name__}",
                    status_code=None,
                    response_body=None,
                ) from e

            except Exception as e:
                logger.error(
                    "action_client_unexpected_error",
                    endpoint=resolved_endpoint,
                    error=str(e),
                )
                raise WorkflowActionError(
                    message=f"외부 API 호출 중 예기치 않은 오류: {str(e)}",
                    status_code=None,
                    response_body=None,
                ) from e

        # max_retries 초과 (이론적으로 도달 불가하지만 방어적 처리)
        raise WorkflowActionError(
            message=f"외부 API 호출 실패: 최대 재시도 초과",
            status_code=None,
            response_body=None,
        ) from last_error

    @staticmethod
    def _parse_response_body(response: httpx.Response) -> dict:
        """응답 바디를 JSON dict로 파싱한다. 실패 시 텍스트를 감싼다."""
        try:
            return response.json()
        except Exception:
            return {"raw": response.text[:1000]}

    async def close(self) -> None:
        """httpx 클라이언트를 닫는다."""
        await self._client.aclose()
