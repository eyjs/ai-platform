# AI Platform 외부 연동 가이드

외부 시스템(FlowSNS, 사주앱 등)이 AI Platform API를 통해 챗봇, 문서 수집, 세션 관리 기능을 연동하기 위한 가이드.

---

## 1. 인증

모든 인증 필수 엔드포인트는 아래 두 가지 방식 중 하나를 사용합니다.

### API Key (서버 간 통신용, 권장)

```
X-API-Key: aip_xxxxxxxxxxxx
```

- ADMIN이 `POST /api/api-keys`로 발급
- 발급 시 1회만 표시되므로 안전하게 보관
- `user_role`, `security_level_max`, `allowed_profiles`, `rate_limit_per_min` 설정 가능

### JWT (사용자 직접 인증용)

```
Authorization: Bearer <jwt_token>
```

---

## 2. 기본 URL

```
http://<ai-platform-host>:<port>/api
```

개발: `http://localhost:8020/api`

---

## 3. 엔드포인트 목록

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| GET | `/health` | 불필요 | 서버 상태 확인 |
| GET | `/profiles` | 불필요 | 사용 가능한 챗봇 프로필 목록 |
| POST | `/chat` | 필수 | 챗봇 질의 (동기, JSON 응답) |
| POST | `/chat/stream` | 필수 | 챗봇 질의 (SSE 스트리밍) |
| GET | `/sessions` | 필수 | 대화 세션 목록 조회 |
| GET | `/sessions/{session_id}/history` | 필수 | 대화 히스토리 조회 |
| POST | `/documents/ingest` | 필수 | 문서 수집 요청 (비동기) |
| GET | `/documents/ingest/{job_id}` | 필수 | 문서 수집 작업 상태 |
| GET | `/workflows` | 불필요 | 워크플로우 목록 |
| POST | `/workflow/start` | 필수 | 워크플로우 시작 |
| POST | `/workflow/advance` | 필수 | 워크플로우 진행 |
| POST | `/api-keys` | ADMIN | API Key 발급 |
| POST | `/feedback` | 필수 | 응답 피드백 제출 |
| GET | `/admin/feedback` | ADMIN | 피드백 목록 조회 |

---

## 4. 챗봇 연동

### 4.1 프로필 확인

```bash
GET /api/profiles
```

```json
[
  { "id": "flowsns-ops", "name": "FlowBot", "mode": "agentic", "domains": [] },
  { "id": "saju-master", "name": "사주마스터", "mode": "rag", "domains": ["saju"] }
]
```

### 4.2 동기 질의 (POST /chat)

한 번에 전체 응답을 받습니다. 짧은 질의에 적합.

**Request:**
```bash
curl -X POST http://localhost:8020/api/chat \
  -H "X-API-Key: aip_xxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "오늘 남은 태스크가 뭐야?",
    "chatbot_id": "flowsns-ops",
    "session_id": null,
    "context": null,
    "metadata": {
      "company_id": "uuid-here",
      "user_id": "uuid-here",
      "source": "flowsns"
    }
  }'
```

**파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `question` | string | O | 사용자 질문 (최대 5000자) |
| `chatbot_id` | string | - | 프로필 ID. null이면 오케스트레이터 자동 라우팅 |
| `session_id` | string | - | 세션 ID. null이면 자동 생성 후 응답에 포함 |
| `context` | string | - | 외부 컨텍스트 (시스템 프롬프트에 주입) |
| `metadata` | object | - | 외부 시스템 메타데이터. 도구 실행 시 전달됨 |

**Response:**
```json
{
  "answer": "오늘 남은 태스크는 3건입니다:\n1. ...",
  "sources": [],
  "trace": null,
  "response_id": "uuid-for-feedback"
}
```

### 4.3 스트리밍 질의 (POST /chat/stream)

실시간 토큰 스트리밍. 챗봇 UI에 권장.

**Request:** `/chat`과 동일한 body

**Response:** Server-Sent Events (SSE) 스트림

```
event: token
data: {"delta": "오늘 "}

event: token
data: {"delta": "남은 태스크는 "}

event: token
data: {"delta": "3건입니다."}

event: done
data: {"answer": "오늘 남은 태스크는 3건입니다.", "session_id": "uuid-session", "profile_id": "flowsns-ops", "response_id": "uuid-response", "confidence": null, "traversal_path": []}
```

**SSE 이벤트 타입:**

| event | data 필드 | 설명 |
|-------|-----------|------|
| `token` | `{"delta": "..."}` | 스트리밍 토큰 (누적하여 표시) |
| `replace` | `{"delta": "..."}` | 기존 내용을 대체 (도구 실행 후 재작성) |
| `trace` | `{"step": "...", ...}` | 내부 처리 추적 (디버깅용) |
| `done` | `{"answer": "...", "session_id": "...", ...}` | 완료. 전체 답변 + 세션 ID |
| `error` | `{"detail": "..."}` | 오류 발생 |

**프론트엔드 파싱 예시 (TypeScript):**

```typescript
const response = await fetch('/api/chat/stream', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-API-Key': 'aip_xxxxxxxxxxxx',
  },
  body: JSON.stringify({ question, chatbot_id: 'flowsns-ops', session_id }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = '';
let currentEvent = '';
let fullAnswer = '';

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });
  const lines = buffer.split('\n');
  buffer = lines.pop() || '';

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      currentEvent = line.slice(7).trim();
      continue;
    }
    if (!line.startsWith('data: ')) continue;

    const parsed = JSON.parse(line.slice(6));

    if (currentEvent === 'token') {
      fullAnswer += parsed.delta;
      updateUI(fullAnswer);
    } else if (currentEvent === 'done') {
      fullAnswer = parsed.answer;
      sessionId = parsed.session_id;  // 다음 요청에 재사용
      updateUI(fullAnswer);
    }
  }
}
```

---

## 5. 세션 / 히스토리

대화 세션은 서버에 자동 저장됩니다. 세션 ID를 보관하면 이후 대화를 이어갈 수 있고, 히스토리를 조회할 수 있습니다.

### 5.1 세션 목록 조회

```bash
GET /api/sessions?profile_id=flowsns-ops&limit=20&offset=0
```

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `profile_id` | string | - | 프로필 ID로 필터 (선택) |
| `limit` | int | 20 | 최대 100 |
| `offset` | int | 0 | 페이지네이션 오프셋 |

**Response:**
```json
{
  "sessions": [
    {
      "session_id": "uuid-session-1",
      "profile_id": "flowsns-ops",
      "created_at": "2026-05-13T10:00:00+00:00",
      "updated_at": "2026-05-13T11:30:00+00:00",
      "turn_count": 8
    }
  ],
  "total": 1
}
```

### 5.2 대화 히스토리 조회

```bash
GET /api/sessions/{session_id}/history?max_turns=50
```

| 파라미터 | 타입 | 기본값 | 설명 |
|----------|------|--------|------|
| `max_turns` | int | 50 | 최대 200 |

**Response:**
```json
{
  "session_id": "uuid-session-1",
  "profile_id": "flowsns-ops",
  "turns": [
    { "role": "user", "content": "오늘 남은 태스크", "timestamp": "2026-05-13T10:00:00+00:00" },
    { "role": "assistant", "content": "3건 남았습니다...", "timestamp": "2026-05-13T10:00:05+00:00" }
  ],
  "created_at": "2026-05-13T10:00:00+00:00",
  "updated_at": "2026-05-13T11:30:00+00:00"
}
```

**접근 제어:** 세션 소유자(user_id 일치) 또는 ADMIN만 조회 가능. 403 반환.

---

## 6. 문서 수집 (RAG)

외부 문서를 AI Platform에 색인하여 RAG 검색에 활용합니다.

### 6.1 문서 수집 요청

```bash
POST /api/documents/ingest
```

```json
{
  "title": "마케팅 전략 가이드",
  "content": "본문 텍스트...",
  "domain_code": "marketing",
  "security_level": "PUBLIC",
  "metadata": { "author": "홍길동" }
}
```

**Response (202 Accepted):**
```json
{ "job_id": "uuid-job", "status": "queued" }
```

### 6.2 작업 상태 확인

```bash
GET /api/documents/ingest/{job_id}
```

```json
{
  "job_id": "uuid-job",
  "status": "completed",
  "result": { "document_id": "uuid-doc", "chunks": 5 },
  "error": null,
  "attempts": 1
}
```

`status`: `queued` → `processing` → `completed` | `failed`

---

## 7. 피드백

사용자가 챗봇 응답에 좋아요/싫어요를 남길 수 있습니다.

```bash
POST /api/feedback
```

```json
{
  "response_id": "uuid-from-done-event",
  "score": 1,
  "comment": "정확한 답변이었습니다"
}
```

- `score`: `1` (좋아요) 또는 `-1` (싫어요)
- `response_id`: 챗봇 응답의 `done` 이벤트에서 받은 `response_id`

---

## 8. 워크플로우 (순차 챗봇)

사전 정의된 단계별 대화 흐름을 실행합니다.

```bash
# 워크플로우 목록
GET /api/workflows

# 시작
POST /api/workflow/start
{ "workflow_id": "onboarding", "session_id": null }

# 진행
POST /api/workflow/advance
{ "session_id": "uuid-session", "input": "사용자 입력" }
```

---

## 9. 연동 체크리스트

1. **API Key 발급** — ADMIN에게 요청하여 적절한 권한의 키 발급
2. **프로필 확인** — `GET /profiles`로 사용할 챗봇 프로필 ID 확인
3. **스트리밍 파서 구현** — SSE `event:` + `data:` 라인 파싱 (섹션 4.3 참고)
4. **세션 ID 관리** — 첫 응답의 `done` 이벤트에서 `session_id` 수신 → 저장 → 이후 요청에 재사용
5. **히스토리 로딩** — 앱 재시작 시 `GET /sessions/{id}/history`로 이전 대화 복원
6. **피드백 연동** (선택) — `response_id`를 보관하여 사용자 피드백 전송
7. **metadata 활용** — `company_id`, `user_id` 등 외부 시스템 컨텍스트를 metadata에 전달하면 도구 실행 시 참조됨

---

## 10. 에러 응답

모든 에러는 HTTP 상태 코드 + JSON body로 반환됩니다.

```json
{ "detail": "에러 메시지" }
```

| 코드 | 의미 |
|------|------|
| 400 | 잘못된 요청 (빈 question, 유효하지 않은 ID 등) |
| 401 | 인증 실패 (API Key/JWT 누락 또는 만료) |
| 403 | 권한 부족 (프로필 접근 불가, 세션 소유자 불일치) |
| 404 | 리소스 없음 (세션, 프로필, 작업 등) |
| 429 | Rate limit 초과 |
| 500 | 서버 내부 오류 |
| 503 | 서비스 미초기화 |

---

## 11. Rate Limiting

API Key별로 분당 요청 수 제한이 적용됩니다 (기본 60/분). 초과 시 429 반환.
