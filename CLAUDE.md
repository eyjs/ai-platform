# AI Platform — Monorepo Root

## 프로젝트 개요

Profile 기반 범용 AI 에이전트 플랫폼의 모노레포.
ChatGPT GPTs처럼 설정(YAML)만으로 도메인별 AI 챗봇을 생성/운영한다.
Agent는 하나(Universal Agent), 행동은 Profile이 결정한다.

이 파일은 **워크스페이스 전역 규칙**만 다룬다. 각 앱의 기술스택·빌드 명령·구조는 해당 앱의 `CLAUDE.md`를 참조한다.

## 모노레포 구조 (2-depth)

```
ai-platform/
├── CLAUDE.md                 # ← 이 파일 (워크스페이스 전역 규칙)
├── package.json              # npm workspaces 루트
├── docker-compose.yml        # PostgreSQL 16 + pgvector (단일 인프라)
├── docs/                     # 아키텍처 문서, ADR, 회고
├── apps/
│   ├── api/                  # Python 3.11 / FastAPI (Universal Agent Runtime)
│   │   └── CLAUDE.md         # ← apps/api 전용 컨텍스트
│   ├── bff/                  # TypeScript / NestJS 10 (관리자 BFF)
│   │   └── CLAUDE.md
│   └── frontend/             # TypeScript / Next.js 15 / React 19
│       └── CLAUDE.md
└── packages/
    └── design-system/        # CSS 변수 토큰 (빌드 없음)
        └── CLAUDE.md
```

## 워크스페이스 구성

- **매니저**: npm workspaces (Node 20+)
- **패키지 prefix**: `@aip/`
- **루트 `package.json` workspaces**: `apps/frontend`, `apps/bff`, `packages/*`
- **`apps/api`는 npm workspace에 포함되지 않는다.** Python 독립 패키지이며 루트 `package.json`의 스크립트에도 api 관련 명령이 없다 (정상)

## 공통 명령

루트에서 실행 가능한 명령 (현재 `package.json` 기준):

```bash
npm run dev:frontend     # Next.js 개발 서버 (port 3000)
npm run dev:bff          # NestJS 개발 서버 (watch 모드)
npm run build:frontend   # Next.js 프로덕션 빌드
npm run build:bff        # NestJS 프로덕션 빌드
npm run lint             # 모든 워크스페이스 lint (--if-present)
npm run typecheck        # 모든 워크스페이스 typecheck (--if-present)
```

`apps/api`는 위 명령에 포함되지 **않는다.** Python 앱 실행은 `apps/api/CLAUDE.md` 참조.

## 포트 매핑

| 앱 | 기본 포트 | dev 명령 |
|---|---|---|
| apps/api | 8000 | `uvicorn src.main:app --reload --port 8000` (apps/api 디렉토리에서) |
| apps/frontend | 3000 | `npm run dev:frontend` (루트에서) |
| apps/bff | 4000 | `PORT=4000 npm run dev:bff` (루트에서) |

> bff의 `main.ts`는 `PORT` 환경변수를 사용한다. 미지정 시 NestJS 기본값이 적용되므로 충돌 방지를 위해 항상 `PORT=4000`을 지정한다.

## 앱간 경계 규칙 (절대 규칙)

1. **앱간 직접 import 금지**
   - ❌ `apps/frontend`에서 `apps/bff/src/...`를 상대경로로 직접 import 금지
   - ❌ `apps/bff`에서 `apps/api/src/...`를 import 금지 (그리고 기술적으로 불가능 — 언어가 다름)
   - ✅ 앱간 소통은 **HTTP** 또는 **DB**를 통해서만
   - ✅ 공유 타입/토큰이 필요하면 `packages/*`에 배치

2. **레이어 간 의존은 단방향**
   ```
   frontend ──HTTP──▶ bff ──HTTP──▶ api
                        └──SQL──▶ PostgreSQL ◀──SQL── api
   ```
   - frontend는 api를 직접 호출할 수도 있다 (SSE 채팅 스트리밍). 이는 허용되지만, 그 외 CRUD는 bff 경유.
   - api는 bff/frontend의 존재를 알지 **않는다.** 역방향 참조 금지.

3. **공통 패키지 사용 규칙**
   - `@aip/design-system`은 `apps/frontend`에서만 사용한다
   - bff/api는 디자인 토큰을 참조하지 않는다

## 커밋 컨벤션

- **포맷**: `type(scope): description`
- **type**: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `style`, `perf`
- **scope**: 변경된 앱/패키지 (예: `api`, `bff`, `frontend`, `design-system`, `deploy`, `docs`)
- **description**: 현재 시제, 소문자 시작, 마침표 없음. 한국어 허용
- **예시**:
  - `feat(api): add intent classifier for 8-type questions`
  - `fix(bff): 프로필 히스토리 기록 누락 수정`
  - `chore(deploy): Vercel 환경변수 갱신`

**브랜치**:
- `feature/*`, `bugfix/*`, `refactor/*`
- `main`: 운영 브랜치 (직접 커밋 지양)

**하나의 커밋 = 하나의 논리적 변경.** "WIP" 커밋, 빌드 깨지는 커밋 금지.

## 환경변수 네이밍 규칙

- **공통 prefix**: `AIP_` (AI Platform 전체 공통)
- 앱별 env 파일은 각 앱 루트에 위치 (`.env`, `.env.local`)
- **절대 커밋 금지**: `.env`, `.env.local`, 자격증명 포함 파일

**앱별 예시**:

| 앱 | 예시 환경변수 |
|---|---|
| apps/api | `AIP_DATABASE_URL`, `AIP_LLM_PROVIDER`, `AIP_EMBEDDING_PROVIDER`, `AIP_JWT_SECRET` |
| apps/bff | `AIP_DATABASE_URL`, `AIP_JWT_SECRET` (api와 공유), `PORT`, `CORS_ORIGIN` |
| apps/frontend | `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_BFF_URL` (Next.js는 `NEXT_PUBLIC_` prefix 필수) |

> `AIP_JWT_SECRET`은 api와 bff가 **공유**한다. bff가 발급한 JWT로 api 인증이 통과해야 한다.

## 인프라 규칙

- **PostgreSQL 단일 스택**. Redis, Elasticsearch, MongoDB 등 추가 인프라 도입 금지.
- 세션 캐시, 작업 큐, Pub/Sub, TTL 만료 모두 PostgreSQL로 해결한다 (자세한 내용은 `apps/api/CLAUDE.md`).
- Docker Compose는 루트 `docker-compose.yml` 하나만 사용. 앱별 compose 파일 금지.

## 문서 위치

- 아키텍처/ADR/회고: `docs/`
- 파이프라인 산출물: `.pipeline/` (에이전트 간 소통, gitignore)
- 앱별 세부 가이드: 각 앱의 `CLAUDE.md`

## Claude Code 파이프라인

이 저장소는 `.pipeline/` 디렉토리를 통한 멀티 에이전트 파이프라인을 사용한다. 각 에이전트는 자신이 작업 중인 **앱의 CLAUDE.md만 읽고도 자급자족**할 수 있어야 한다. 이것이 각 앱 CLAUDE.md를 분리한 이유다.

- 루트 CLAUDE.md = 워크스페이스 공통 규칙 (이 파일)
- 각 앱 CLAUDE.md = 해당 앱 전용 컨텍스트 (자급자족)

**에이전트는 자신의 태스크 범위 밖 앱의 CLAUDE.md를 읽지 않는다.** 예: frontend 태스크를 수행하는 Implementor는 `apps/bff/CLAUDE.md`를 읽지 않는다.
