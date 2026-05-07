"""ActionClient 테스트.

respx를 사용하여 외부 HTTP 호출을 모킹한다.
환경변수 치환, 템플릿 렌더링, 재시도 로직, 에러 핸들링을 검증한다.
"""

import os

import httpx
import pytest
import respx

from src.workflow.action_client import (
    ActionClient,
    WorkflowActionError,
    _resolve_env_in_dict,
    _resolve_env_vars,
)


# --- 환경변수 치환 ---


class TestResolveEnvVars:

    def test_simple_var(self, monkeypatch):
        monkeypatch.setenv("TEST_API_URL", "https://api.test.com")
        assert _resolve_env_vars("${TEST_API_URL}/path") == "https://api.test.com/path"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        assert _resolve_env_vars("http://${HOST}:${PORT}") == "http://localhost:8080"

    def test_missing_var_becomes_empty(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        assert _resolve_env_vars("prefix_${NONEXISTENT_VAR}_suffix") == "prefix__suffix"

    def test_no_pattern_unchanged(self):
        assert _resolve_env_vars("plain text") == "plain text"

    def test_resolve_env_in_dict(self, monkeypatch):
        monkeypatch.setenv("AUTH_TOKEN", "secret123")
        d = {"Authorization": "Bearer ${AUTH_TOKEN}", "Content-Type": "application/json"}
        result = _resolve_env_in_dict(d)
        assert result["Authorization"] == "Bearer secret123"
        assert result["Content-Type"] == "application/json"

    def test_resolve_env_in_nested_dict(self, monkeypatch):
        monkeypatch.setenv("INNER_VAL", "nested")
        d = {"outer": {"inner": "${INNER_VAL}"}}
        result = _resolve_env_in_dict(d)
        assert result["outer"]["inner"] == "nested"


# --- ActionClient.call 성공 ---


class TestActionClientSuccess:

    @respx.mock
    async def test_basic_post(self):
        """기본 POST 호출이 성공한다."""
        route = respx.post("https://api.example.com/submit").mock(
            return_value=httpx.Response(200, json={"id": "abc123", "status": "ok"})
        )

        client = ActionClient()
        try:
            result = await client.call(
                endpoint="https://api.example.com/submit",
                method="POST",
                payload={"name": "홍길동"},
                collected={"name": "홍길동"},
            )
            assert result == {"id": "abc123", "status": "ok"}
            assert route.called
        finally:
            await client.close()

    @respx.mock
    async def test_template_rendering_in_endpoint(self, monkeypatch):
        """엔드포인트에서 {{field}} + ${ENV_VAR} 치환이 동작한다."""
        monkeypatch.setenv("BASE_URL", "https://api.test.com")

        route = respx.post("https://api.test.com/users/홍길동").mock(
            return_value=httpx.Response(200, json={"created": True})
        )

        client = ActionClient()
        try:
            result = await client.call(
                endpoint="${BASE_URL}/users/{{name}}",
                method="POST",
                collected={"name": "홍길동"},
            )
            assert result == {"created": True}
            assert route.called
        finally:
            await client.close()

    @respx.mock
    async def test_template_rendering_in_headers(self, monkeypatch):
        """헤더에서 환경변수 + 템플릿 치환이 동작한다."""
        monkeypatch.setenv("API_KEY", "key123")

        route = respx.post("https://api.example.com/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        client = ActionClient()
        try:
            await client.call(
                endpoint="https://api.example.com/data",
                method="POST",
                headers={"X-API-Key": "${API_KEY}", "X-User": "{{user_id}}"},
                collected={"user_id": "u_42"},
            )
            assert route.called
            request = route.calls[0].request
            assert request.headers["x-api-key"] == "key123"
            assert request.headers["x-user"] == "u_42"
        finally:
            await client.close()

    @respx.mock
    async def test_payload_template_rendering(self):
        """페이로드에서 {{field}} 치환이 동작한다."""
        route = respx.post("https://api.example.com/submit").mock(
            return_value=httpx.Response(200, json={"received": True})
        )

        client = ActionClient()
        try:
            await client.call(
                endpoint="https://api.example.com/submit",
                method="POST",
                payload={"customer": "{{name}}", "phone": "{{phone}}"},
                collected={"name": "김철수", "phone": "010-9876-5432"},
            )
            assert route.called
            import json
            body = json.loads(route.calls[0].request.content)
            assert body["customer"] == "김철수"
            assert body["phone"] == "010-9876-5432"
        finally:
            await client.close()

    @respx.mock
    async def test_get_method(self):
        """GET 메서드가 동작한다."""
        route = respx.get("https://api.example.com/status").mock(
            return_value=httpx.Response(200, json={"alive": True})
        )

        client = ActionClient()
        try:
            result = await client.call(
                endpoint="https://api.example.com/status",
                method="GET",
            )
            assert result == {"alive": True}
            assert route.called
        finally:
            await client.close()


# --- ActionClient.call 에러 ---


class TestActionClientErrors:

    @respx.mock
    async def test_http_4xx_raises_error(self):
        """HTTP 400 에러가 WorkflowActionError로 변환된다."""
        respx.post("https://api.example.com/submit").mock(
            return_value=httpx.Response(400, json={"error": "bad request"})
        )

        client = ActionClient()
        try:
            with pytest.raises(WorkflowActionError) as exc_info:
                await client.call(
                    endpoint="https://api.example.com/submit",
                    method="POST",
                )
            assert exc_info.value.status_code == 400
            assert "400" in str(exc_info.value)
        finally:
            await client.close()

    @respx.mock
    async def test_http_5xx_raises_error(self):
        """HTTP 500 에러가 WorkflowActionError로 변환된다."""
        respx.post("https://api.example.com/submit").mock(
            return_value=httpx.Response(500, json={"error": "internal"})
        )

        client = ActionClient()
        try:
            with pytest.raises(WorkflowActionError) as exc_info:
                await client.call(
                    endpoint="https://api.example.com/submit",
                    method="POST",
                )
            assert exc_info.value.status_code == 500
        finally:
            await client.close()

    @respx.mock
    async def test_http_error_no_retry(self):
        """HTTP 에러는 재시도하지 않는다."""
        route = respx.post("https://api.example.com/submit").mock(
            return_value=httpx.Response(422, json={"detail": "unprocessable"})
        )

        client = ActionClient()
        try:
            with pytest.raises(WorkflowActionError):
                await client.call(
                    endpoint="https://api.example.com/submit",
                    method="POST",
                    max_retries=3,
                )
            # HTTP 에러는 재시도하지 않으므로 1회만 호출
            assert route.call_count == 1
        finally:
            await client.close()

    @respx.mock
    async def test_timeout_retries(self):
        """타임아웃 시 재시도한다."""
        route = respx.post("https://api.example.com/submit").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        client = ActionClient()
        try:
            with pytest.raises(WorkflowActionError) as exc_info:
                await client.call(
                    endpoint="https://api.example.com/submit",
                    method="POST",
                    max_retries=2,
                )
            assert exc_info.value.status_code is None
            assert "연결 실패" in str(exc_info.value)
            # 초기 1회 + 재시도 2회 = 3회
            assert route.call_count == 3
        finally:
            await client.close()

    @respx.mock
    async def test_connect_error_retries_then_succeeds(self):
        """첫 번째 시도 실패 후 두 번째 시도에서 성공."""
        route = respx.post("https://api.example.com/submit")
        route.side_effect = [
            httpx.ConnectError("refused"),
            httpx.Response(200, json={"ok": True}),
        ]

        client = ActionClient()
        try:
            result = await client.call(
                endpoint="https://api.example.com/submit",
                method="POST",
                max_retries=1,
            )
            assert result == {"ok": True}
            assert route.call_count == 2
        finally:
            await client.close()

    @respx.mock
    async def test_non_json_response(self):
        """비-JSON 응답이 raw 텍스트로 감싸진다."""
        respx.post("https://api.example.com/submit").mock(
            return_value=httpx.Response(200, text="plain text response")
        )

        client = ActionClient()
        try:
            result = await client.call(
                endpoint="https://api.example.com/submit",
                method="POST",
            )
            assert "raw" in result
            assert result["raw"] == "plain text response"
        finally:
            await client.close()


# --- ActionClient lifecycle ---


class TestActionClientLifecycle:

    async def test_close(self):
        """close()가 정상적으로 동작한다."""
        client = ActionClient()
        await client.close()
        # 닫은 후에도 에러 없이 종료
