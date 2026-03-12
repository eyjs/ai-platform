"""코어 엔진 E2E 검증 시퀀스.

Golden Dataset(6개 MD 문서, 197 chunks)으로:
1. Deterministic RAG 모드 — 검색 품질 + 출처 정확성
2. SSE 스트리밍 — 지연 없는 토큰 스트리밍 + 출처 반환
3. 도메인 격리 — insurance-qa vs general-chat
4. 크로스도메인 검색 — 자동차보험 프로필로 실손보험 질문
5. Agentic 모드 — fallback 동작 (chat_model 없는 환경)
"""

import json
import sys
import time
import httpx
import asyncio

BASE_URL = "http://localhost:8010/api"
ADMIN_KEY = "aip_dev_admin"
HEADERS = {"X-API-Key": ADMIN_KEY}


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def run():
    async with httpx.AsyncClient(timeout=120.0) as client:

        # =============================================================
        # 1. Deterministic RAG — 자동차보험 자기신체사고
        # =============================================================
        section("1. Deterministic RAG: 자기신체사고 보장 한도")

        t0 = time.time()
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "자동차보험에서 자기신체사고 보장 한도가 어떻게 돼?",
            "chatbot_id": "insurance-qa",
        }, headers=HEADERS)
        elapsed = (time.time() - t0) * 1000

        print(f"  Status: {r.status_code}")
        print(f"  Latency: {elapsed:.0f}ms")

        if r.status_code == 200:
            data = r.json()
            print(f"  Mode: {data['trace']['mode']}")
            print(f"  Tools: {data['trace']['tools_called']}")
            print(f"  Sources: {len(data['sources'])}")
            for s in data['sources']:
                print(f"    - [{s.get('method','?')}] {s['title']} (score={s['score']:.3f})")
                print(f"      {s['chunk_text'][:120]}...")
            print(f"\n  Answer:\n{data['answer'][:500]}")
        else:
            print(f"  ERROR: {r.text[:200]}")

        # =============================================================
        # 2. SSE 스트리밍 — 면책사유
        # =============================================================
        section("2. SSE Streaming: 자동차보험 면책사유")

        t0 = time.time()
        tokens = []
        trace_events = []
        done_data = None
        first_token_ms = None

        try:
            async with client.stream("POST", f"{BASE_URL}/chat/stream", json={
                "question": "자동차보험 면책사유가 뭐야? 구체적으로 알려줘",
                "chatbot_id": "insurance-qa",
            }, headers=HEADERS) as r:
                print(f"  Status: {r.status_code}")
                current_event = ""
                async for line in r.aiter_lines():
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        data_str = line.split(":", 1)[1].strip()
                        if current_event == "token":
                            if first_token_ms is None:
                                first_token_ms = (time.time() - t0) * 1000
                            tokens.append(data_str)
                        elif current_event == "trace":
                            trace_events.append(json.loads(data_str))
                        elif current_event == "done":
                            done_data = json.loads(data_str)
        except Exception as e:
            print(f"  Stream error: {e}")

        total_ms = (time.time() - t0) * 1000
        answer = "".join(tokens)

        print(f"  First token: {first_token_ms:.0f}ms" if first_token_ms else "  First token: N/A")
        print(f"  Total: {total_ms:.0f}ms")
        print(f"  Tokens received: {len(tokens)}")
        print(f"  Trace events: {len(trace_events)}")
        for te in trace_events:
            print(f"    - {te}")

        if done_data:
            print(f"  Done.tools_called: {done_data.get('tools_called', [])}")
            print(f"  Done.sources: {len(done_data.get('sources', []))}")
            for s in done_data.get("sources", []):
                print(f"    - {s['title']} (score={s.get('score', 0):.3f})")
        else:
            print("  WARNING: done event not received!")

        print(f"\n  Answer ({len(answer)} chars):\n{answer[:500]}")

        # =============================================================
        # 3. 도메인 격리 — insurance-qa로 보험법규 질문
        # =============================================================
        section("3. 도메인 격리: insurance-qa → 보험법규 접근")

        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "보험업법 제95조 내용을 자세히 알려줘",
            "chatbot_id": "insurance-qa",
        }, headers=HEADERS)
        if r.status_code == 200:
            data = r.json()
            sources = data['sources']
            # insurance-qa의 domain_scopes에 보험법규가 없으므로
            # 보험법규 도메인 문서가 없어야 정상
            has_law_source = any("보험업법" in s["title"] for s in sources)
            print(f"  Sources: {len(sources)}")
            print(f"  Contains 보험업법 source: {has_law_source}")
            for s in sources:
                print(f"    - [{s.get('method','?')}] {s['title']}")
            if has_law_source:
                print("  WARNING: 도메인 격리 실패 — 보험법규 문서가 검색됨")
            else:
                print("  OK: 도메인 격리 정상 (보험법규 문서 미포함)")
            print(f"\n  Answer:\n{data['answer'][:300]}")

        # =============================================================
        # 4. general-chat으로 동일 질문 (도메인 제한 없음)
        # =============================================================
        section("4. general-chat: 보험법규 접근 (도메인 제한 없음)")

        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "보험업법 제95조 내용을 자세히 알려줘",
            "chatbot_id": "general-chat",
        }, headers=HEADERS)
        if r.status_code == 200:
            data = r.json()
            sources = data['sources']
            has_law_source = any("보험업법" in s["title"] for s in sources)
            print(f"  Sources: {len(sources)}")
            print(f"  Contains 보험업법 source: {has_law_source}")
            for s in sources:
                print(f"    - [{s.get('method','?')}] {s['title']}")
            if has_law_source:
                print("  OK: general-chat이 보험법규 문서에 정상 접근")
            else:
                print("  WARNING: general-chat에서도 보험법규 문서 미접근")
            print(f"\n  Answer:\n{data['answer'][:300]}")

        # =============================================================
        # 5. 실손보험 도수치료 질문
        # =============================================================
        section("5. 실손보험 RAG: 도수치료 보장 횟수")

        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "실손보험 약관에서 도수치료 보장 횟수가 어떻게 되나요?",
            "chatbot_id": "insurance-qa",
        }, headers=HEADERS)
        if r.status_code == 200:
            data = r.json()
            print(f"  Mode: {data['trace']['mode']}")
            print(f"  Tools: {data['trace']['tools_called']}")
            print(f"  Sources: {len(data['sources'])}")
            for s in data['sources']:
                print(f"    - [{s.get('method','?')}] {s['title']} (score={s['score']:.3f})")
            print(f"\n  Answer:\n{data['answer'][:500]}")

        # =============================================================
        # 6. Agentic 모드 (fallback 검증)
        # =============================================================
        section("6. Agentic 모드: general-assistant (fallback)")

        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "내 보험 계약 상태 조회해주고, 실손보험 약관에서 도수치료 보장 횟수도 같이 찾아줘",
            "chatbot_id": "general-assistant",
        }, headers=HEADERS)
        if r.status_code == 200:
            data = r.json()
            print(f"  Mode: {data['trace']['mode']}")
            print(f"  Tools: {data['trace']['tools_called']}")
            print(f"  Sources: {len(data['sources'])}")
            # chat_model이 없으면 deterministic fallback
            print(f"\n  Answer:\n{data['answer'][:500]}")
        else:
            print(f"  Status: {r.status_code}")
            print(f"  Response: {r.text[:200]}")

        # =============================================================
        # 7. 인사 질문 (needs_rag=False 경로)
        # =============================================================
        section("7. Greeting: needs_rag=False 경로")

        t0 = time.time()
        r = await client.post(f"{BASE_URL}/chat", json={
            "question": "안녕하세요! 반갑습니다.",
            "chatbot_id": "insurance-qa",
        }, headers=HEADERS)
        elapsed = (time.time() - t0) * 1000

        if r.status_code == 200:
            data = r.json()
            print(f"  Mode: {data['trace']['mode']}")
            print(f"  Tools: {data['trace']['tools_called']}")
            print(f"  Latency: {elapsed:.0f}ms")
            print(f"  Answer: {data['answer'][:200]}")
            if not data['trace']['tools_called']:
                print("  OK: RAG 미실행 (direct_generate 경로)")
            else:
                print("  WARNING: 인사인데 RAG 실행됨")

    print(f"\n{'='*60}")
    print("  검증 완료")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(run())
