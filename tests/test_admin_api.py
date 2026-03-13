"""Admin API 테스트.

Control Plane CRUD 엔드포인트를 검증한다.
DB 없이 ProfileStore/WorkflowStore의 메모리 캐시만 사용한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent.profile import AgentProfile, ToolRef
from src.agent.profile_store import ProfileStore
from src.domain.models import AgentMode
from src.gateway.admin_router import admin_router
from src.gateway.models import UserContext
from src.workflow.definition import WorkflowDefinition, WorkflowStep
from src.workflow.store import WorkflowStore


def _create_test_app() -> FastAPI:
    """테스트용 FastAPI 앱을 생성한다."""
    app = FastAPI()
    app.include_router(admin_router, prefix="/api")

    # Mock auth (항상 ADMIN)
    mock_auth = AsyncMock()
    mock_auth.authenticate = AsyncMock(return_value=UserContext(
        user_id="test-admin",
        user_role="ADMIN",
        security_level_max="SECRET",
        allowed_profiles=[],
        allowed_origins=[],
        rate_limit_per_min=120,
    ))
    mock_auth.check_origin = MagicMock()  # sync 메서드

    # Mock ProfileStore (DB 없이)
    profile_store = MagicMock(spec=ProfileStore)
    profile_store._cache = {}
    profile_store.profile_count = 0
    profile_store.invalidate_cache = MagicMock()

    # 실제 파싱/직렬화 로직 사용 (public 메서드)
    profile_store.parse_profile = ProfileStore._parse_profile
    profile_store.profile_to_dict = lambda self_or_p: (
        {**ProfileStore._profile_to_dict(self_or_p),
         "id": self_or_p.id, "name": self_or_p.name,
         "description": self_or_p.description}
    )

    async def mock_list_all():
        return list(profile_store._cache.values())

    async def mock_get(pid):
        return profile_store._cache.get(pid)

    async def mock_create(p):
        profile_store._cache[p.id] = p

    async def mock_update(p):
        if p.id in profile_store._cache:
            profile_store._cache[p.id] = p
            return True
        return False

    async def mock_delete(pid):
        return profile_store._cache.pop(pid, None) is not None

    profile_store.list_all = mock_list_all
    profile_store.get = mock_get
    profile_store.create = mock_create
    profile_store.update = mock_update
    profile_store.delete = mock_delete

    # Mock WorkflowStore (DB 없이)
    workflow_store = WorkflowStore()

    app.state.auth_service = mock_auth
    app.state.profile_store = profile_store
    app.state.workflow_store = workflow_store

    return app


@pytest.fixture
def client():
    app = _create_test_app()
    return TestClient(app)


# --- Profile CRUD ---


class TestProfileCRUD:

    def test_list_profiles_empty(self, client):
        resp = client.get("/api/admin/profiles", headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_get_profile(self, client):
        body = {
            "id": "test-bot",
            "name": "테스트 봇",
            "description": "테스트용 챗봇",
            "mode": "agentic",
            "system_prompt": "당신은 테스트 챗봇입니다.",
            "domain_scopes": ["test/domain"],
            "tools": [{"name": "rag_search", "config": {"max_vector_chunks": 3}}],
        }
        resp = client.post("/api/admin/profiles", json=body, headers={"X-API-Key": "test"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "test-bot"
        assert data["name"] == "테스트 봇"
        assert data["description"] == "테스트용 챗봇"
        assert data["mode"] == "agentic"
        assert data["domain_scopes"] == ["test/domain"]

        # GET으로 재확인
        resp = client.get("/api/admin/profiles/test-bot", headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert resp.json()["system_prompt"] == "당신은 테스트 챗봇입니다."

    def test_create_duplicate_returns_409(self, client):
        body = {"id": "dup-bot", "name": "중복 봇"}
        client.post("/api/admin/profiles", json=body, headers={"X-API-Key": "test"})
        resp = client.post("/api/admin/profiles", json=body, headers={"X-API-Key": "test"})
        assert resp.status_code == 409

    def test_update_profile(self, client):
        # 생성
        client.post("/api/admin/profiles", json={"id": "upd-bot", "name": "원래 이름"},
                     headers={"X-API-Key": "test"})

        # 부분 수정
        resp = client.put("/api/admin/profiles/upd-bot",
                          json={"name": "바뀐 이름", "system_prompt": "새 프롬프트"},
                          headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "바뀐 이름"
        assert resp.json()["system_prompt"] == "새 프롬프트"

    def test_update_nonexistent_returns_404(self, client):
        resp = client.put("/api/admin/profiles/ghost",
                          json={"name": "없는 프로필"},
                          headers={"X-API-Key": "test"})
        assert resp.status_code == 404

    def test_delete_profile(self, client):
        client.post("/api/admin/profiles", json={"id": "del-bot", "name": "삭제 대상"},
                     headers={"X-API-Key": "test"})
        resp = client.delete("/api/admin/profiles/del-bot", headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        resp = client.get("/api/admin/profiles/del-bot", headers={"X-API-Key": "test"})
        assert resp.status_code == 404

    def test_list_profiles_after_create(self, client):
        client.post("/api/admin/profiles", json={"id": "bot-a", "name": "봇 A"},
                     headers={"X-API-Key": "test"})
        client.post("/api/admin/profiles", json={"id": "bot-b", "name": "봇 B"},
                     headers={"X-API-Key": "test"})
        resp = client.get("/api/admin/profiles", headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        ids = [p["id"] for p in resp.json()]
        assert "bot-a" in ids
        assert "bot-b" in ids


# --- Workflow CRUD ---


class TestWorkflowCRUD:

    def test_list_workflows_empty(self, client):
        resp = client.get("/api/admin/workflows", headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_get_workflow(self, client):
        body = {
            "id": "test-flow",
            "name": "테스트 워크플로우",
            "description": "예약 접수용",
            "escape_policy": "allow",
            "max_retries": 5,
            "steps": [
                {"id": "welcome", "type": "message", "prompt": "환영합니다.", "next": "ask_name"},
                {"id": "ask_name", "type": "input", "prompt": "이름을 입력하세요.", "save_as": "name", "next": "done"},
                {"id": "done", "type": "message", "prompt": "{{name}}님, 완료!"},
            ],
        }
        resp = client.post("/api/admin/workflows", json=body, headers={"X-API-Key": "test"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "test-flow"
        assert data["name"] == "테스트 워크플로우"
        assert data["escape_policy"] == "allow"
        assert data["max_retries"] == 5
        assert len(data["steps"]) == 3

        # GET으로 재확인
        resp = client.get("/api/admin/workflows/test-flow", headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert resp.json()["steps"][2]["prompt"] == "{{name}}님, 완료!"

    def test_create_duplicate_returns_409(self, client):
        body = {"id": "dup-flow", "name": "중복", "steps": []}
        client.post("/api/admin/workflows", json=body, headers={"X-API-Key": "test"})
        resp = client.post("/api/admin/workflows", json=body, headers={"X-API-Key": "test"})
        assert resp.status_code == 409

    def test_update_workflow(self, client):
        body = {
            "id": "upd-flow", "name": "원래",
            "steps": [{"id": "s1", "type": "message", "prompt": "원래 메시지"}],
        }
        client.post("/api/admin/workflows", json=body, headers={"X-API-Key": "test"})

        resp = client.put("/api/admin/workflows/upd-flow",
                          json={"name": "수정됨", "max_retries": 10},
                          headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "수정됨"
        assert resp.json()["max_retries"] == 10
        assert len(resp.json()["steps"]) == 1  # steps는 변경 안 함

    def test_update_workflow_steps(self, client):
        body = {
            "id": "step-flow", "name": "스텝 수정",
            "steps": [{"id": "old", "type": "message", "prompt": "old"}],
        }
        client.post("/api/admin/workflows", json=body, headers={"X-API-Key": "test"})

        new_steps = [
            {"id": "new1", "type": "input", "prompt": "이름?", "save_as": "name", "next": "new2"},
            {"id": "new2", "type": "message", "prompt": "완료"},
        ]
        resp = client.put("/api/admin/workflows/step-flow",
                          json={"steps": new_steps},
                          headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert len(resp.json()["steps"]) == 2
        assert resp.json()["steps"][0]["id"] == "new1"

    def test_delete_workflow(self, client):
        body = {"id": "del-flow", "name": "삭제 대상", "steps": []}
        client.post("/api/admin/workflows", json=body, headers={"X-API-Key": "test"})
        resp = client.delete("/api/admin/workflows/del-flow", headers={"X-API-Key": "test"})
        assert resp.status_code == 200

        resp = client.get("/api/admin/workflows/del-flow", headers={"X-API-Key": "test"})
        assert resp.status_code == 404

    def test_created_workflow_runs_in_engine(self, client):
        """Admin API로 생성한 워크플로우가 실제 WorkflowEngine에서 실행되는지 검증."""
        from src.workflow.engine import WorkflowEngine

        body = {
            "id": "api-created",
            "name": "API 생성 워크플로우",
            "steps": [
                {"id": "ask", "type": "input", "prompt": "이름?", "save_as": "name", "next": "done"},
                {"id": "done", "type": "message", "prompt": "{{name}}님 완료!"},
            ],
        }
        client.post("/api/admin/workflows", json=body, headers={"X-API-Key": "test"})

        # WorkflowStore에서 직접 꺼내서 Engine에 넣기
        app = client.app
        store = app.state.workflow_store
        engine = WorkflowEngine(store)

        result = engine.start("api-created", "s1")
        assert "이름?" in result.bot_message

        result = engine.advance("s1", "김관리자")
        assert result.completed
        assert "김관리자" in result.bot_message


# --- Cache Invalidation ---


class TestCacheInvalidation:

    def test_invalidate_cache(self, client):
        resp = client.post("/api/admin/cache/invalidate", headers={"X-API-Key": "test"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
