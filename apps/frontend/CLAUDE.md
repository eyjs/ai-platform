# apps/frontend — Web Client (Next.js 15 / React 19)

> 이 파일은 `apps/frontend` 전용 컨텍스트다. 워크스페이스 전역 규칙은 루트 `CLAUDE.md` 참조. 이 문서는 자급자족(self-contained)이다.

## 역할

AI Platform의 사용자/관리자용 웹 클라이언트.
- 일반 사용자: ChatGPT 스타일 챗봇 UI (SSE 스트리밍)
- 관리자: Profile YAML 편집기 (Monaco Editor), 통합 대시보드

## 기술 스택

| 영역 | 기술 | 버전 |
|---|---|---|
| 프레임워크 | Next.js (App Router) | 15.3 |
| UI | React | 19.1 |
| 언어 | TypeScript | 5.7 |
| 스타일 | Tailwind CSS | 4.1 (PostCSS 방식) |
| 디자인 토큰 | `@aip/design-system` | workspace |
| 마크다운 | `react-markdown` + `remark-gfm` + `rehype-highlight` | |
| 코드 에디터 | `@monaco-editor/react` | 4.7 |
| 유틸 | `clsx`, `tailwind-merge` | |
| 린트 | ESLint 9 (flat config: `eslint.config.mjs`) + `eslint-config-next` | |

패키지명: **`@aip/frontend`** (npm workspace 멤버)

## 디렉토리 구조

```
apps/frontend/
├── CLAUDE.md             # ← 이 파일
├── package.json
├── tsconfig.json
├── next.config.*         # transpilePackages: ['@aip/design-system']
├── postcss.config.*      # Tailwind 4 PostCSS 플러그인
├── app/                  # App Router 엔트리
│   ├── layout.tsx        # 루트 레이아웃 (globals.css import)
│   ├── globals.css       # Tailwind + design-system tokens import
│   └── (...)/page.tsx    # 라우트 그룹
├── components/           # UI 컴포넌트
│   └── ui/               # 공통 프리미티브 (button, card, ...)
├── hooks/                # 커스텀 훅
├── lib/                  # API 클라이언트, 유틸 (cn, auth, ...)
└── types/                # 도메인 타입 정의
```

## Quick Start

```bash
# 루트에서
npm install
npm run dev:frontend            # http://localhost:3000
```

## 명령어

```bash
# 루트에서 실행
npm run dev:frontend            # 개발 서버 (port 3000)
npm run build:frontend          # 프로덕션 빌드
npm run lint --workspace @aip/frontend
npm run typecheck --workspace @aip/frontend

# apps/frontend 디렉토리에서 직접
npx next dev --port 3000
npx next build
npx next start
npx eslint .                    # next lint 아님 — flat config 는 eslint CLI 로 돈다
npx tsc --noEmit
```

## 환경변수

Next.js에서 **클라이언트**에 노출하려면 반드시 `NEXT_PUBLIC_` prefix:

| 변수 | 값 | 용도 |
|---|---|---|
| `NEXT_PUBLIC_FASTAPI_URL` | docker: `http://localhost:8020` / 호스트 uvicorn: `http://localhost:8000` | FastAPI(apps/api) 직접 호출 (SSE 채팅, LLM 엔진 현황) |
| `NEXT_PUBLIC_BFF_URL` | docker: `http://localhost:3011/bff` | NestJS(apps/bff) 호출 (Profile CRUD, 대시보드). **`/bff` prefix 를 포함한 값**이다 — 코드는 `${BFF_URL}/profiles/...` 로 붙이므로 여기에 prefix 가 없으면 404 난다 |

> ⚠️ **`NEXT_PUBLIC_API_URL` 은 존재하지 않는다.** 예전 이 표가 그 이름을 적어놨지만 실제 배선된 이름은
> `NEXT_PUBLIC_FASTAPI_URL` 이다(`lib/api/chat.ts`, `hardware.ts`, `inspect.ts`). 문서만 보고 `NEXT_PUBLIC_API_URL`
> 을 세팅하면 조용히 localhost 기본값으로 폴백한다.

> ⚠️ **포트 주의**: compose 는 api 를 `8020:8000`, bff 를 `3011:3001` 로 매핑한다. 컨테이너 내부 포트(8000/3001)가
> 아니라 **호스트 포트(8020/3011)** 를 써야 한다. `apps/frontend` 에는 `.env*` 파일이 없어 미설정 시 각 클라이언트의
> 하드코딩 기본값으로 떨어지는데, 그 기본값들이 서로 다르다(`3001/bff`, `4000/bff`, `8000`). 로컬에서 붙일 땐
> `.env.local` 로 위 두 변수를 명시할 것.

> **주의**: 클라이언트 컴포넌트에서 `AIP_*` env를 읽을 수 없다. 서버 전용 env는 Server Component 또는 Route Handler에서만 사용.

## Tailwind CSS 4 주의사항

- Tailwind 4는 **PostCSS 기반**이다. `tailwind.config.*` 대신 `@tailwindcss/postcss`가 `postcss.config.*`에 등록되어 있다
- 디자인 토큰은 **CSS 변수**(`var(--color-*)`, `var(--font-size-*)`, `var(--spacing-*)`)로만 사용한다
- 하드코딩 색상(`#hex`, `rgb()`) 금지 → 자동 리뷰 FAIL 트리거
- 하드코딩 폰트 사이즈/간격 금지 → 자동 리뷰 FAIL 트리거

## @aip/design-system 사용법

- 디자인 시스템 패키지는 **빌드 없이** `index.ts`를 직접 export 한다
- `next.config.*`에 `transpilePackages: ['@aip/design-system']`가 설정되어 있어야 Next.js가 런타임 transpile 한다
- CSS 변수 토큰 사용:
  ```ts
  // app/globals.css
  @import "@aip/design-system/tokens.css";
  ```
- 디자인 토큰 수정은 `packages/design-system/tokens.css`에서만 한다 (프론트엔드에서 오버라이드 금지)

## SSR 주의사항

### Monaco Editor는 반드시 dynamic import

Monaco Editor는 브라우저 전용이므로 SSR 시 실패한다. 항상 `dynamic` + `ssr: false`:

```ts
import dynamic from "next/dynamic";
const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.Editor),
  { ssr: false }
);
```

### Route Group 경로 충돌 주의

`app/(chat)/page.tsx`와 `app/page.tsx`를 동시에 두면 라우트 충돌이 발생한다. 최근 배포 이슈에서 `app/page.tsx` 제거로 해결됨. 라우트 그룹을 사용할 때 루트 `page.tsx` 중복 생성 금지.

### Vercel 배포 주의

- `outputFileTracingRoot`가 `next.config.*`에 설정되어 있다 (Next.js route group manifest 버그 회피)
- Vercel 도메인은 CORS 허용 목록에 포함되어야 한다 (apps/bff, apps/api 양쪽)

## 인증

- JWT는 apps/bff가 발급한다. 프론트엔드는 로그인 시 bff에서 토큰을 받아 저장
- 저장 방식: httpOnly cookie 권장 (XSS 방어). 메모리 저장은 새로고침 시 유실
- 채팅 SSE 호출 시 동일 JWT를 `Authorization: Bearer <token>` 헤더로 apps/api에 전달
- Next.js `middleware.ts`에서 미인증 시 `/login` 리다이렉트

## 코딩 컨벤션

- **파일명**: `kebab-case.tsx` (컴포넌트), `kebab-case.ts` (유틸)
- **컴포넌트명**: `PascalCase`
- **훅**: `use` prefix (`useChatStream`)
- **핸들러**: `handle` prefix (`handleClick`, `handleSubmit`)
- **불리언**: `is/has/can/should` prefix
- **상수**: `UPPER_SNAKE_CASE`
- **className 합성**: `clsx` + `tailwind-merge` (`lib/cn.ts`의 `cn` 헬퍼 사용)
- **타입**: `any` 금지. 필요시 `unknown` + 타입 가드
- **커밋**: `feat(frontend):`, `fix(frontend):` 등 conventional commits

## 이 앱에서 하면 안 되는 것

1. ❌ **`apps/bff/src/...` 상대경로 직접 import** (`../../bff/src/...`) — 앱간 경계 위반. HTTP로만 통신
2. ❌ **`apps/api/src/...` 참조** — 언어가 다르며 경계 위반
3. ❌ **하드코딩 색상 (`#hex`, `rgb()`)** — 디자인 토큰(`var(--color-*)`) 사용
4. ❌ **하드코딩 폰트 사이즈/간격** — `var(--font-size-*)`, `var(--spacing-*)` 사용
5. ❌ **Monaco Editor를 직접 import** (SSR 오류) — 반드시 `dynamic(..., { ssr: false })`
6. ❌ **`any` 타입** — 자동 리뷰 FAIL
7. ❌ **`AIP_*` env를 클라이언트 컴포넌트에서 읽기** — `NEXT_PUBLIC_*` 또는 서버 전용
8. ❌ **디자인 시스템 로컬 오버라이드** — `packages/design-system`에서만 수정
9. ❌ **`app/page.tsx` 와 route group `(x)/page.tsx` 동시 생성** — 경로 충돌
10. ❌ **인터랙티브 요소에 focus ring, aria-label 누락** — 디자인 리뷰 FAIL
