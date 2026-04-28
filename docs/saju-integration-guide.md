# saju backend -> ai-platform 통합 가이드

> saju backend가 ai-platform의 리포트 생성 API를 호출하기 위한 클라이언트 구현 가이드.
> 실제 saju 코드 수정은 이 문서 범위 밖이며, saju 팀이 참조하는 문서이다.

## 1. 개요

### 전환 아키텍처

```
[Before]
saju backend -> Redis Queue -> ai-worker -> LLM -> Redis -> saju backend

[After]
saju backend -> ai-platform API (HTTP) -> LLM -> Response
```

- ai-worker 프로세스를 제거하고, ai-platform이 리포트 생성을 전담한다.
- saju backend는 HTTP 클라이언트(`AiPlatformClient`)로 ai-platform을 호출한다.
- ai-platform 내부에서는 `fortune-saju` Profile(YAML)이 리포트 생성 로직을 정의한다.
- 인증은 API Key 방식을 사용하며, admin 대시보드에서 발급한다.

### 주요 이점

- Redis 큐 의존성 제거 (인프라 단순화)
- ai-worker 별도 배포/운영 불필요
- Profile 기반으로 프롬프트/모델/도구를 중앙 관리
- 모니터링/피드백이 ai-platform 대시보드에 통합

---

## 2. API 스펙

### 2.1 리포트 생성 요청

```
POST /api/report/generate
```

**Headers:**

| 헤더 | 값 | 비고 |
|---|---|---|
| `Content-Type` | `application/json` | 필수 |
| `X-API-Key` | `aip_xxxxxxxxxxxxxxxx` | API Key 인증 |

> JWT(`Authorization: Bearer <token>`)도 지원하나, 서버-서버 통신에서는 API Key 권장.

**Request Body:**

```json
{
  "report_type": "paper",
  "saju_data": {
    "name": "홍길동",
    "birth_date": "1990-01-15",
    "birth_time": "14:30",
    "gender": "M",
    "calendar_type": "solar",
    "pillars": {
      "year": "경오",
      "month": "정축",
      "day": "임진",
      "hour": "정미"
    },
    "energy": {
      "wood": 25,
      "fire": 30,
      "earth": 15,
      "metal": 20,
      "water": 10
    },
    "yongsin": "수(水)",
    "shinsal": ["도화살", "천을귀인"],
    "daewoon": [
      { "age_start": 1, "age_end": 10, "pillar": "무인" },
      { "age_start": 11, "age_end": 20, "pillar": "기묘" }
    ],
    "sewoon": [
      { "year": 2026, "pillar": "병오" }
    ]
  },
  "metadata": {
    "requester_id": "user-uuid",
    "callback_url": "https://saju.example.com/webhook/report"
  }
}
```

**report_type 값:**

| 값 | 설명 | 섹션 수 |
|---|---|---|
| `paper` | 종합 사주 분석 리포트 | 7 |
| `compatibility` | 궁합 분석 리포트 | 6 |

**Response (202 Accepted):**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

### 2.2 상태 조회

```
GET /api/report/status/{job_id}
```

**Headers:** `X-API-Key` 동일

**Response (200):**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "generating",
  "sections_completed": 5,
  "sections_total": 7,
  "error": null
}
```

**status 값:**

| 값 | 설명 |
|---|---|
| `queued` | 대기 중 |
| `generating` | 생성 중 (sections_completed로 진행률 확인) |
| `completed` | 완료 |
| `failed` | 실패 (error 필드에 사유) |

### 2.3 결과 조회

```
GET /api/report/result/{job_id}
```

**Headers:** `X-API-Key` 동일

**Response (200):**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "report_type": "paper",
  "report_data": {
    "sections": [
      {
        "key": "personality",
        "title": "성격 및 기질",
        "content": "..."
      }
    ],
    "summary": "...",
    "generated_at": "2026-04-27T10:30:00Z"
  }
}
```

> `status`가 `completed`가 아니면 404를 반환한다.

---

## 3. 인증 방법

### API Key 발급

1. ai-platform admin 대시보드 접속 (`/admin/api-keys`)
2. "새 API Key 생성" 클릭
3. 설정:
   - 이름: `saju-backend-production`
   - 허용 Profile: `fortune-saju` 선택
   - Rate Limit: 분당 60 (기본값)
   - Security Level Max: `internal`
4. 생성 후 표시되는 키(`aip_xxxxx...`)를 안전하게 저장
   - 키는 생성 시 한 번만 표시된다

### 환경변수 설정 (saju backend)

```env
AIP_BASE_URL=https://ai-platform.example.com
AIP_API_KEY=aip_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## 4. saju backend 구현 예시 (Python)

```python
import asyncio
import httpx
from dataclasses import dataclass


@dataclass
class ReportResult:
    job_id: str
    status: str
    report_type: str
    report_data: dict


class AiPlatformClient:
    """ai-platform 리포트 생성 API 클라이언트."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            },
            timeout=timeout,
        )

    async def request_report(self, report_type: str, saju_data: dict, metadata: dict | None = None) -> str:
        """리포트 생성 요청 -> job_id 반환."""
        body = {
            "report_type": report_type,
            "saju_data": saju_data,
        }
        if metadata:
            body["metadata"] = metadata

        res = await self._client.post("/api/report/generate", json=body)
        res.raise_for_status()
        return res.json()["job_id"]

    async def poll_status(self, job_id: str, timeout: int = 300, interval: int = 5) -> dict:
        """완료까지 폴링 (interval초 간격, timeout초 제한)."""
        elapsed = 0
        while elapsed < timeout:
            res = await self._client.get(f"/api/report/status/{job_id}")
            res.raise_for_status()
            data = res.json()

            if data["status"] == "completed":
                return data
            if data["status"] == "failed":
                raise RuntimeError(f"리포트 생성 실패: {data.get('error', 'unknown')}")

            await asyncio.sleep(interval)
            elapsed += interval

        raise TimeoutError(f"리포트 생성 타임아웃 ({timeout}초)")

    async def get_result(self, job_id: str) -> ReportResult:
        """완성된 리포트 조회."""
        res = await self._client.get(f"/api/report/result/{job_id}")
        res.raise_for_status()
        data = res.json()
        return ReportResult(
            job_id=data["job_id"],
            status=data["status"],
            report_type=data["report_type"],
            report_data=data["report_data"],
        )

    async def generate_and_wait(self, report_type: str, saju_data: dict, **kwargs) -> ReportResult:
        """편의 메서드: 요청 + 폴링 + 결과 조회를 한 번에."""
        job_id = await self.request_report(report_type, saju_data, kwargs.get("metadata"))
        await self.poll_status(job_id, timeout=kwargs.get("timeout", 300))
        return await self.get_result(job_id)

    async def close(self):
        await self._client.aclose()
```

### 사용 예시

```python
async def generate_paper_report(saju_data: dict, user_id: str) -> dict:
    client = AiPlatformClient(
        base_url=settings.AIP_BASE_URL,
        api_key=settings.AIP_API_KEY,
    )
    try:
        result = await client.generate_and_wait(
            report_type="paper",
            saju_data=saju_data,
            metadata={"requester_id": user_id},
            timeout=300,
        )
        return result.report_data
    finally:
        await client.close()
```

---

## 5. 마이그레이션 체크리스트

### 준비 단계

- [ ] ai-platform에 `fortune-saju` Profile이 배포되어 있는지 확인
- [ ] admin 대시보드에서 API Key 발급
- [ ] API Key에 `fortune-saju` Profile 바인딩 확인
- [ ] saju backend 환경변수에 `AIP_BASE_URL`, `AIP_API_KEY` 설정

### 구현 단계

- [ ] `AiPlatformClient` 클래스를 saju backend에 추가
- [ ] 기존 Redis queue producer 코드 대신 `AiPlatformClient` 호출로 교체
- [ ] 에러 핸들링: 타임아웃, 네트워크 오류, API 오류 처리
- [ ] 리트라이 로직 추가 (지수 백오프 권장, 최대 3회)

### 검증 단계

- [ ] paper 리포트 생성 E2E 테스트 (7섹션 모두 생성 확인)
- [ ] compatibility 리포트 생성 E2E 테스트 (6섹션 모두 생성 확인)
- [ ] 리포트 JSON 스키마가 기존 ai-worker 출력과 호환되는지 확인
- [ ] saju frontend에서 리포트 렌더링 정상 동작 확인

### 정리 단계

- [ ] Redis queue producer 코드 제거
- [ ] ai-worker 프로세스 중단 및 배포 파이프라인에서 제거
- [ ] ai-worker 관련 환경변수 정리
- [ ] 모니터링: ai-platform 대시보드에서 요청 로그 확인

---

## 6. 호환성 매핑

### 6.1 task_type -> report_type 매핑

| ai-worker `task_type` | ai-platform `report_type` | 비고 |
|---|---|---|
| `saju_paper` | `paper` | 종합 사주 분석 |
| `saju_compatibility` | `compatibility` | 궁합 분석 |

### 6.2 입력 필드 매핑

| ai-worker 필드 | ai-platform `saju_data` 필드 | 변환 |
|---|---|---|
| `user_name` | `name` | 키 이름만 변경 |
| `birth_date` | `birth_date` | 동일 (YYYY-MM-DD) |
| `birth_time` | `birth_time` | 동일 (HH:mm) |
| `gender` | `gender` | 동일 (M/F) |
| `calendar_type` | `calendar_type` | 동일 (solar/lunar) |
| `four_pillars` | `pillars` | 키 이름 변경, 내부 구조 동일 |
| `five_elements` | `energy` | 키 이름 변경, 내부 구조 동일 |
| `yong_shin` | `yongsin` | 키 이름 변경 |
| `shin_sal_list` | `shinsal` | 키 이름 변경 |
| `dae_woon` | `daewoon` | 키 이름 변경, 배열 구조 동일 |
| `se_woon` | `sewoon` | 키 이름 변경, 배열 구조 동일 |

### 6.3 출력 스키마 매핑

| ai-worker 출력 필드 | ai-platform `report_data` 필드 | 비고 |
|---|---|---|
| `result.sections[]` | `sections[]` | 동일 구조 |
| `result.sections[].section_key` | `sections[].key` | 키 이름 변경 |
| `result.sections[].section_title` | `sections[].title` | 키 이름 변경 |
| `result.sections[].content` | `sections[].content` | 동일 |
| `result.summary` | `summary` | 동일 |
| `result.created_at` | `generated_at` | 키 이름 변경 |

---

## 7. 오류 처리 가이드

### HTTP 상태 코드

| 코드 | 의미 | 대응 |
|---|---|---|
| 202 | 생성 요청 수락 | 정상 -- 폴링 시작 |
| 400 | 잘못된 요청 (saju_data 검증 실패) | 입력 데이터 확인 |
| 401 | 인증 실패 | API Key 확인 |
| 403 | 권한 없음 (Profile 미바인딩) | API Key의 허용 Profile 확인 |
| 404 | 잡 미존재 | job_id 확인 |
| 429 | Rate Limit 초과 | 대기 후 재시도 |
| 500 | 서버 오류 | 재시도 (지수 백오프) |

### 재시도 전략

```python
import asyncio
from random import uniform

async def with_retry(fn, max_retries=3, base_delay=1.0):
    for attempt in range(max_retries):
        try:
            return await fn()
        except (httpx.HTTPStatusError, httpx.ConnectError) as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt) + uniform(0, 1)
            await asyncio.sleep(delay)
```
