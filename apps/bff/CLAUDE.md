# apps/bff — Admin BFF (NestJS 10 / TypeORM / PostgreSQL)

> 이 파일은 `apps/bff` 전용 컨텍스트다. 워크스페이스 전역 규칙은 루트 `CLAUDE.md` 참조. 이 문서는 자급자족(self-contained)이다.

## 역할

관리자용 Backend-for-Frontend. apps/frontend의 관리자 화면(Profile CRUD, 대시보드)을 위한 API를 제공한다.
- JWT 인증 발급 (apps/api와 secret 공유)
- Profile CRUD + 변경 히스토리
- 대시보드 집계 쿼리
- **apps/api는 직접 호출하지 않는다.** DB를 직접 읽는다 (동일 PostgreSQL)

## 기술 스택

| 영역 | 기술 | 버전 |
|---|---|---|
| 프레임워크 | NestJS | 10.4 |
| 언어 | TypeScript | 5.7 |
| ORM | TypeORM | 0.3 |
| DB | PostgreSQL | 16 (apps/api와 동일 인스턴스) |
| 인증 | `@nestjs/jwt` + `@nestjs/passport` + `passport-jwt` | |
| 검증 | `class-validator` + `class-transformer` | |
| 해싱 | `bcrypt` | |
| YAML | `js-yaml` | |
| 린트 | ESLint 9 | |

패키지명: **`@aip/bff`** (npm workspace 멤버)

### `@nestjs/cli` 의존성 주의

`nest build`, `nest start --watch` 명령은 `@nestjs/cli` (devDependencies)가 설치되어 있어야 동작한다. `package.json`에 이미 포함되어 있으며 **제거 금지**. CI에서 `npm ci` 후 `npx nest build`가 가능해야 한다.

## 디렉토리 구조

```
apps/bff/
├── CLAUDE.md             # ← 이 파일
├── package.json
├── tsconfig.json
├── nest-cli.json         # nest build 진입점 설정
├── src/
│   ├── main.ts           # NestFactory.create, CORS, ValidationPipe, global prefix /bff
│   ├── app.module.ts     # 루트 모듈
│   ├── app.controller.ts
│   ├── config/           # database.config, jwt.config
│   ├── auth/             # JWT 인증 모듈 (controller, service, strategy, guards)
│   ├── profiles/         # Profile CRUD 모듈
│   ├── dashboard/        # 대시보드 집계 모듈
│   ├── entities/         # TypeORM Entity (web_users, agent_profiles, profile_history)
│   └── migrations/       # TypeORM 마이그레이션
└── dist/                 # nest build 산출물 (gitignore)
```

## Quick Start

```bash
# 루트에서
npm install
PORT=4000 npm run dev:bff       # http://localhost:4000/bff
```

## 명령어

```bash
# 루트에서
npm run dev:bff                 # nest start --watch
npm run build:bff               # nest build
npm run lint --workspace @aip/bff
npm run typecheck --workspace @aip/bff

# apps/bff 디렉토리에서 직접
npx nest start --watch          # 개발 (watch)
npx nest build                  # 프로덕션 빌드
node dist/main                  # 프로덕션 실행
npx eslint "{src,test}/**/*.ts"
npx tsc --noEmit

# TypeORM 마이그레이션 (빌드 후 dist 기준)
npx nest build
npx typeorm migration:run -d dist/config/database.config.js
npx typeorm migration:revert -d dist/config/database.config.js
```

## 포트 및 Global Prefix

- 기본 포트: **4000** (`PORT` env로 오버라이드). 프론트엔드가 3000이므로 반드시 다른 포트 사용
- Global prefix: `/bff` (`app.setGlobalPrefix('bff')`). 모든 엔드포인트는 `/bff/...` 로 시작
- CORS origin 기본값: `http://localhost:3000, https://ai-platform-eight-sigma.vercel.app` (`CORS_ORIGIN` env로 오버라이드, 콤마 구분)

## API 엔드포인트 (예정/구현)

| Method | Path | 설명 |
|---|---|---|
| POST | `/bff/auth/login` | 로그인 (email + password) → JWT 발급 |
| POST | `/bff/auth/refresh` | 토큰 갱신 |
| GET | `/bff/auth/me` | 현재 사용자 |
| GET | `/bff/profiles` | Profile 목록 |
| GET | `/bff/profiles/:id` | Profile 상세 (YAML 포함) |
| POST | `/bff/profiles` | 생성 |
| PUT | `/bff/profiles/:id` | 수정 (자동 히스토리 기록) |
| DELETE | `/bff/profiles/:id` | 삭제 |
| PATCH | `/bff/profiles/:id/activate` | 활성화 |
| GET | `/bff/profiles/:id/history` | 변경 이력 |
| POST | `/bff/profiles/:id/restore` | 버전 복원 |
| GET | `/bff/dashboard/summary` | 현황 요약 |
| GET | `/bff/dashboard/usage?period=today\|7d\|30d` | Profile별 사용량 |
| GET | `/bff/dashboard/latency?period=...` | 레이턴시 시계열 |
| GET | `/bff/dashboard/logs?page=1&size=10` | 대화 로그 |

## apps/api와의 관계

- **HTTP 호출 금지가 기본.** bff는 apps/api의 엔드포인트를 호출하지 않는다
- **PostgreSQL 직접 접속.** 동일 DB의 `agent_profiles` 테이블을 TypeORM Entity로 매핑
- **JWT Secret 공유.** bff가 발급한 JWT로 apps/api의 인증이 통과되어야 한다 (`AIP_JWT_SECRET` 동일 값 사용)
- **스키마 변경 주의.** `agent_profiles` 테이블 스키마는 apps/api(alembic)가 소유한다. bff는 해당 스키마를 **읽고/쓰기** 하지만 `ALTER TABLE`하지 않는다. bff 고유 테이블(`web_users`, `profile_history`)은 bff의 TypeORM 마이그레이션으로 관리

## 환경변수

| 변수 | 예시 | 설명 |
|---|---|---|
| `PORT` | `4000` | 서버 포트 (미지정 시 NestJS 기본값) |
| `AIP_DATABASE_URL` | `postgresql://user:pass@localhost:5432/aip` | apps/api와 동일 DB |
| `AIP_JWT_SECRET` | (공유) | apps/api와 동일 값 |
| `CORS_ORIGIN` | `http://localhost:3000,https://...` | 콤마 구분 허용 origin |

## 인증 흐름

1. 사용자가 `/bff/auth/login`에 email + password POST
2. bff가 `web_users` 테이블에서 bcrypt 검증
3. 성공 시 Access Token (15분) + Refresh Token (7일) 발급
4. JWT payload는 apps/api 스키마와 호환: `sub`, `role`, `security_level_max`, `user_type`
5. 프론트엔드는 이 토큰을 apps/api SSE 호출에도 동일하게 사용

## 코딩 컨벤션

- **파일명**: `kebab-case.ts` (NestJS 관례: `auth.controller.ts`, `create-profile.dto.ts`)
- **클래스명**: `PascalCase`
- **DI**: constructor injection 사용
- **DTO**: `class-validator` 데코레이터로 검증 (`@IsString()`, `@IsEmail()`)
- **ValidationPipe**: `whitelist: true`, `forbidNonWhitelisted: true`, `transform: true` (이미 main.ts에 설정됨) — 우회 금지
- **에러 응답 포맷**: `{ success: false, error: { code, message, details } }`
- **HTTP 상태 코드**: 적절히 사용 (400 vs 404 vs 409 vs 500)
- **커밋**: `feat(bff):`, `fix(bff):` 등 conventional commits
- **타입**: `any` 금지

## 이 앱에서 하면 안 되는 것

1. ❌ **apps/api의 코드를 import** — 언어가 다르기도 하고, 앱간 경계 위반
2. ❌ **apps/api의 HTTP 엔드포인트 호출** — bff는 DB 직접 접속. api 호출이 필요하다고 느껴지면 설계 재검토
3. ❌ **apps/frontend 코드 import** — 앱간 경계 위반
4. ❌ **`agent_profiles` 테이블 스키마 변경** — apps/api(alembic) 소유. bff는 읽기/쓰기만
5. ❌ **`any` 타입** — 자동 리뷰 FAIL
6. ❌ **`ValidationPipe` 우회** — 입력 검증은 boundary에서 항상
7. ❌ **하드코딩 시크릿** — `AIP_*` env 사용
8. ❌ **`@nestjs/cli` devDependencies 제거** — `nest build` 동작 불가
9. ❌ **global prefix `/bff` 제거** — 프론트엔드와 Nginx/Vercel 라우팅이 이 prefix를 가정
10. ❌ **Redis, 별도 캐시 서버 도입** — PostgreSQL 단일 스택 원칙
