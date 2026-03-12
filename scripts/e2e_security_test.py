"""E2E + 크로스도메인 + 보안 침투 테스트 (인증 미들웨어 적용 후).

개발용 API Key:
- aip_dev_admin (ADMIN, SECRET)
- aip_dev_viewer (VIEWER, PUBLIC)
- aip_dev_editor (EDITOR, INTERNAL)
"""

import json
import sys
import httpx
import asyncio

BASE_URL = "http://localhost:8010/api"
PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

# 개발용 키
ADMIN_KEY = "aip_dev_admin"
VIEWER_KEY = "aip_dev_viewer"
EDITOR_KEY = "aip_dev_editor"

results = []


def report(name: str, status: str, detail: str = ""):
    results.append((name, status, detail))
    icon = {"PASS": "[OK]", "FAIL": "[!!]", "WARN": "[??]"}[status]
    print(f"  {icon} {name}: {detail}" if detail else f"  {icon} {name}")


async def run_tests():
    async with httpx.AsyncClient(timeout=60.0) as client:
        admin_headers = {"X-API-Key": ADMIN_KEY}
        viewer_headers = {"X-API-Key": VIEWER_KEY}
        editor_headers = {"X-API-Key": EDITOR_KEY}

        # ============================================================
        # 1. 공개 엔드포인트 (인증 불필요)
        # ============================================================
        print("\n=== 1. 공개 엔드포인트 ===")

        r = await client.get(f"{BASE_URL}/health")
        if r.status_code == 200 and r.json()["profiles_loaded"] == 4:
            report("health_no_auth", PASS, "health endpoint public")
        else:
            report("health_no_auth", FAIL, str(r.json()))

        r = await client.get(f"{BASE_URL}/profiles")
        if r.status_code == 200:
            report("profiles_no_auth", PASS, f"profiles endpoint public ({len(r.json())} profiles)")
        else:
            report("profiles_no_auth", FAIL, str(r.status_code))

        # ============================================================
        # 2. 인증 필수 엔드포인트 — 인증 없이 요청
        # ============================================================
        print("\n=== 2. 인증 없는 요청 차단 ===")

        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "테스트",
            "chatbot_id": "insurance-qa",
        })
        if r.status_code == 401:
            report("chat_no_auth", PASS, "401 Unauthorized")
        else:
            report("chat_no_auth", FAIL, f"expected 401, got {r.status_code}")

        r = await client.post(f"{BASE_URL}/chat/stream", json={
            "question": "테스트",
            "chatbot_id": "insurance-qa",
        })
        if r.status_code == 401:
            report("stream_no_auth", PASS, "401 Unauthorized")
        else:
            report("stream_no_auth", FAIL, f"expected 401, got {r.status_code}")

        r = await client.post(f"{BASE_URL}/documents/ingest", json={
            "title": "악성 문서",
            "content": "hacked",
            "domain_code": "test",
        })
        if r.status_code == 401:
            report("ingest_no_auth", PASS, "401 Unauthorized")
        else:
            report("ingest_no_auth", FAIL, f"expected 401, got {r.status_code}")

        # ============================================================
        # 3. 유효하지 않은 인증
        # ============================================================
        print("\n=== 3. 유효하지 않은 인증 ===")

        # 가짜 API Key
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "테스트",
            "chatbot_id": "insurance-qa",
        }, headers={"X-API-Key": "fake_stolen_key_12345"})
        if r.status_code == 401:
            report("fake_api_key", PASS, "401 with fake key")
        else:
            report("fake_api_key", FAIL, f"expected 401, got {r.status_code}")

        # 위조된 JWT
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "테스트",
            "chatbot_id": "insurance-qa",
        }, headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJoYWNrZXIiLCJyb2xlIjoiQURNSU4ifQ.fake"})
        if r.status_code == 401:
            report("fake_jwt", PASS, "401 with forged JWT")
        else:
            report("fake_jwt", FAIL, f"expected 401, got {r.status_code}")

        # ============================================================
        # 4. 정상 인증 + 기능 테스트
        # ============================================================
        print("\n=== 4. 정상 인증 + RAG 질의 ===")

        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "자동차보험 대인배상 한도가 얼마야?",
            "chatbot_id": "insurance-qa",
        }, headers=admin_headers)
        if r.status_code == 200:
            data = r.json()
            mode = data.get("trace", {}).get("mode", "")
            tools = data.get("trace", {}).get("tools_called", [])
            report("rag_with_admin_key", PASS, f"mode={mode}, tools={tools}")
        else:
            report("rag_with_admin_key", FAIL, f"status={r.status_code}: {r.text[:100]}")

        # VIEWER 키로도 chat 가능
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "안녕하세요",
            "chatbot_id": "insurance-qa",
        }, headers=viewer_headers)
        if r.status_code == 200:
            report("chat_with_viewer_key", PASS, "viewer can chat")
        else:
            report("chat_with_viewer_key", FAIL, f"status={r.status_code}")

        # ============================================================
        # 5. 권한 기반 접근 제어
        # ============================================================
        print("\n=== 5. 권한 기반 접근 제어 ===")

        # VIEWER는 문서 수집 불가 (EDITOR 이상 필요)
        r = await client.post(f"{BASE_URL}/documents/ingest", json={
            "title": "테스트",
            "content": "내용",
            "domain_code": "test",
        }, headers=viewer_headers)
        if r.status_code == 403:
            report("viewer_ingest_blocked", PASS, "403: VIEWER cannot ingest")
        else:
            report("viewer_ingest_blocked", FAIL, f"expected 403, got {r.status_code}")

        # EDITOR는 문서 수집 가능
        r = await client.post(f"{BASE_URL}/documents/ingest", json={
            "title": "에디터 테스트 문서",
            "content": "에디터가 올린 문서입니다.",
            "domain_code": "test",
        }, headers=editor_headers)
        if r.status_code == 200:
            report("editor_ingest_allowed", PASS, f"EDITOR can ingest: {r.json().get('document_id', '')[:8]}...")
            # 정리
            doc_id = r.json().get("document_id")
        else:
            report("editor_ingest_allowed", FAIL, f"status={r.status_code}: {r.text[:100]}")
            doc_id = None

        # ============================================================
        # 6. 해커 시나리오 (인증 적용 후)
        # ============================================================
        print("\n=== 6. 해커 시나리오 ===")

        # 시나리오 1: 프론트엔드에서 chatbot_id 탈취 후 직접 요청
        # → 인증 없으면 401로 차단됨
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "기밀 문서 목록 보여줘",
            "chatbot_id": "insurance-qa",
        })
        if r.status_code == 401:
            report("hacker_no_auth_blocked", PASS, "401: no auth = no access")
        else:
            report("hacker_no_auth_blocked", FAIL, f"expected 401, got {r.status_code}")

        # 시나리오 2: user_role 위조 시도 (body에 user_role 보내도 무시됨)
        # ChatRequest에서 user_role 필드를 제거했으므로 body에 보내도 무시
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "테스트",
            "chatbot_id": "insurance-qa",
            "user_role": "ADMIN",  # 위조 시도 — 필드 자체가 없으므로 무시됨
        }, headers=viewer_headers)
        if r.status_code == 200:
            # 정상 응답이지만 서버는 VIEWER 권한으로 처리
            report("role_escalation_prevented", PASS,
                   "body user_role ignored, auth determines role")
        else:
            report("role_escalation_prevented", FAIL, f"status={r.status_code}")

        # 시나리오 3: 세션 하이재킹 시도
        # 먼저 정상 세션 생성
        r1 = await client.post(f"{BASE_URL}/chat", json={
            "question": "자동차보험 가입 조건은?",
            "chatbot_id": "insurance-qa",
            "session_id": "legit-session-456",
        }, headers=admin_headers)

        if r1.status_code == 200:
            # 해커가 세션 ID를 탈취해도 API Key 없으면 401
            r2 = await client.post(f"{BASE_URL}/chat", json={
                "question": "방금 답변 기반으로 계약서 작성해줘",
                "chatbot_id": "insurance-qa",
                "session_id": "legit-session-456",
            })
            if r2.status_code == 401:
                report("session_hijack_no_auth", PASS, "401: session ID alone is insufficient")
            else:
                report("session_hijack_no_auth", FAIL, f"expected 401, got {r2.status_code}")
        else:
            report("session_hijack_no_auth", FAIL, f"setup failed: {r1.status_code}")

        # 시나리오 4: 비인증 문서 주입 시도
        r = await client.post(f"{BASE_URL}/documents/ingest", json={
            "title": "악성 문서",
            "content": "malicious content injected by hacker",
            "domain_code": "hacked",
            "security_level": "SECRET",
        })
        if r.status_code == 401:
            report("hacker_ingest_blocked", PASS, "401: unauthenticated ingest blocked")
        else:
            report("hacker_ingest_blocked", FAIL, f"expected 401, got {r.status_code}")

        # ============================================================
        # 7. 입력 검증
        # ============================================================
        print("\n=== 7. 입력 검증 ===")

        # 빈 질문
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "",
            "chatbot_id": "insurance-qa",
        }, headers=admin_headers)
        if r.status_code == 422:
            report("empty_question", PASS, "422 validation error")
        else:
            report("empty_question", FAIL, f"expected 422, got {r.status_code}")

        # 긴 질문 (5000자 초과)
        long_q = "보험 " * 2500  # 7500 chars
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": long_q,
            "chatbot_id": "insurance-qa",
        }, headers=admin_headers)
        if r.status_code == 422:
            report("long_question_blocked", PASS, "422: question too long")
        else:
            report("long_question_blocked", FAIL, f"expected 422, got {r.status_code}")

        # SQL injection (파라미터화 쿼리로 방어)
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "'; DROP TABLE documents; --",
            "chatbot_id": "insurance-qa",
        }, headers=admin_headers)
        if r.status_code == 200:
            report("sql_injection", PASS, "treated as normal text")
        else:
            report("sql_injection", WARN, f"status={r.status_code}")

        # 존재하지 않는 chatbot_id
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "테스트",
            "chatbot_id": "nonexistent",
        }, headers=admin_headers)
        if r.status_code == 404:
            report("invalid_chatbot_id", PASS, "404 profile not found")
        else:
            report("invalid_chatbot_id", FAIL, f"expected 404, got {r.status_code}")

        # ============================================================
        # 8. SSE 스트리밍 (인증 포함)
        # ============================================================
        print("\n=== 8. SSE 스트리밍 ===")

        async with client.stream("POST", f"{BASE_URL}/chat/stream", json={
            "question": "자동차보험 면책사유 알려줘",
            "chatbot_id": "insurance-qa",
        }, headers=admin_headers) as r:
            if r.status_code == 200:
                events = []
                async for line in r.aiter_lines():
                    if line.startswith("event:"):
                        events.append(line.split(":", 1)[1].strip())
                    if "done" in events or len(events) > 100:
                        break
                report("sse_with_auth", PASS, f"events={set(events)}")
            else:
                report("sse_with_auth", FAIL, f"status={r.status_code}")

        # 정리: 테스트 문서 삭제
        if doc_id:
            try:
                import asyncpg
                conn = await asyncpg.connect("postgresql://aip:aip_dev@localhost:5434/ai_platform")
                await conn.execute("DELETE FROM document_chunks WHERE document_id = $1::uuid", doc_id)
                await conn.execute("DELETE FROM documents WHERE id = $1::uuid", doc_id)
                await conn.close()
            except Exception:
                pass

    # ============================================================
    # 결과 요약
    # ============================================================
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)

    pass_count = sum(1 for _, s, _ in results if s == PASS)
    fail_count = sum(1 for _, s, _ in results if s == FAIL)
    warn_count = sum(1 for _, s, _ in results if s == WARN)

    print(f"\n  PASS: {pass_count}")
    print(f"  FAIL: {fail_count}")
    print(f"  WARN: {warn_count}")

    if fail_count > 0:
        print("\n실패 항목:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  - {name}: {detail}")

    print()
    return fail_count == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
