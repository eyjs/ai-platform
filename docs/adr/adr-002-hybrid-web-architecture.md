# ADR-002: 웹앱 하이브리드 아키텍처 — SSE 직접 호출 vs BFF 경유 분리

## 상태
Accepted (2026-04-06)

## 컨텍스트

AI Platform 웹 애플리케이션을 구축할 때, 프론트엔드(Next.js)에서
기존 FastAPI 백엔드와 신규 NestJS BFF 사이의 라우팅 전략을 결정해야 했다.

다음 두 요구사항이 상충했다:
1. SSE 스트리밍 채팅: 지연 최소화가 핵심. 모든 토큰이 실시간으로 렌더링되어야 한다.
2. Profile CRUD / 대시보드: 히스토리 기록, 감사 로그, YAML 검증 등 부가 처리가 필요하다.

## 결정 과정

### 옵션 1: 모든 트래픽을 NestJS BFF 경유 (거부)

프론트엔드 → NestJS BFF → FastAPI 형태로 SSE를 포함한 모든 요청을 BFF가 중계한다.

거부 이유:
- SSE 프록시 구현 복잡도가 높다 (NestJS에서 ReadableStream 중계)
- 프록시 레이어로 인한 지연(latency) 추가 — 스트리밍에서 체감 가능
- BFF가 실패할 경우 채팅 기능 전체 영향

### 옵션 2: 모든 트래픽을 FastAPI 직접 (거부)

프론트엔드가 FastAPI를 직접 호출하고 BFF를 사용하지 않는다.

거부 이유:
- Profile 변경 히스토리, 감사 로그를 FastAPI에 추가해야 해서 기존 아키텍처 원칙(FastAPI 재구현 금지) 위반
- 관리 기능을 FastAPI에 쌓으면 레이어 책임 분리가 무너진다

### 옵션 3: 역할별 라우팅 분리 — 하이브리드 (채택)

```
채팅 SSE:   프론트엔드 → FastAPI 직접 (/api/chat/stream)
채팅 비스트리밍: 프론트엔드 → FastAPI 직접 (/api/chat)
Profile CRUD: 프론트엔드 → NestJS BFF → PostgreSQL
대시보드:   프론트엔드 → NestJS BFF → PostgreSQL
인증:       프론트엔드 → NestJS BFF (JWT 발급, 동일 HS256 시크릿으로 FastAPI 호환)
```

채택 이유:
- 각 요청 유형이 요구하는 특성(지연 최소화 vs 부가 처리)에 맞는 경로를 선택할 수 있다
- FastAPI는 기존 코드 변경 없이 그대로 활용
- BFF 장애가 채팅 기능에 영향을 주지 않는다 (장애 격리)

## 결정

다음 라우팅 규칙을 최종 확정한다:

| 기능 | 경로 | 근거 |
|------|------|------|
| 채팅 SSE 스트리밍 | 프론트엔드 → FastAPI 직접 | 지연 최소화 |
| 채팅 비스트리밍 | 프론트엔드 → FastAPI 직접 | 일관성 |
| Profile CRUD | 프론트엔드 → NestJS BFF | 히스토리, 검증, 감사 |
| 대시보드 집계 | 프론트엔드 → NestJS BFF | 복잡한 집계 쿼리 |
| 인증 | 프론트엔드 → NestJS BFF | JWT 발급 및 갱신 |
| 문서 수집 | 프론트엔드 → FastAPI 직접 | 기존 엔드포인트 재사용 |

JWT 시크릿을 NestJS BFF와 FastAPI가 공유하여, BFF가 발급한 토큰을 FastAPI가 그대로 검증한다.

## 결과

- Next.js 빌드 성공 (8 라우트, 102kB shared JS)
- TypeScript 오류 0개
- 채팅 SSE와 관리 기능의 독립적인 장애 격리 달성
- 남은 과제: FastAPI CORS 설정에 웹앱 Origin(`localhost:3000`) 추가 필요

## 관련 파일

- `web/apps/frontend/middleware.ts`
- `web/apps/frontend/lib/api/chat.ts`
- `web/apps/bff/src/auth/`
- `web/apps/bff/src/profiles/`
