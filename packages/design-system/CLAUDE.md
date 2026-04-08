# packages/design-system — 디자인 토큰 패키지

> 이 파일은 `packages/design-system` 전용 컨텍스트다. 워크스페이스 전역 규칙은 루트 `CLAUDE.md` 참조. 이 문서는 자급자족(self-contained)이다.

## 역할

AI Platform의 **디자인 토큰 정의 전용 패키지**. 색상, 폰트, 간격 등 CSS 변수를 중앙 관리한다. 컴포넌트는 제공하지 않으며, 앱별 커스터마이징도 지원하지 않는다. "한 곳에서 수정, 모든 앱에 반영"이 원칙이다.

## 기술 스택

| 영역 | 내용 |
|---|---|
| 언어 | TypeScript (타입 export 용) |
| 빌드 | **없음** (`main: "./index.ts"` — 소스 직접 export) |
| 스타일 | CSS 변수 (`tokens.css`) |
| 배포 | npm workspace 내부 전용, 외부 publish 없음 |

패키지명: **`@aip/design-system`** (npm workspace 멤버, private)

## 디렉토리 구조

```
packages/design-system/
├── CLAUDE.md             # ← 이 파일
├── package.json
├── index.ts              # 타입/상수 export 진입점
└── tokens.css            # CSS 변수 정의 (color, font-size, spacing, radius, shadow, z-index)
```

`package.json` exports:
```json
{
  "main": "./index.ts",
  "exports": {
    ".": "./index.ts",
    "./tokens.css": "./tokens.css"
  }
}
```

## 빌드 없음 (중요)

- **빌드 스크립트가 존재하지 않는다.** `dist/` 디렉토리 생성 금지
- `index.ts`를 소비 앱이 직접 참조하므로, 소비 앱의 번들러가 transpile 해야 한다
- Next.js(apps/frontend)는 `next.config.*`의 `transpilePackages: ['@aip/design-system']` 설정 필요
- NestJS(apps/bff)는 디자인 토큰을 사용하지 않는다 (서버 전용)

## 앱에서 사용하는 방법

### apps/frontend (Next.js)

```ts
// app/globals.css
@import "@aip/design-system/tokens.css";
```

```tsx
// 컴포넌트에서
<div style={{ color: "var(--color-primary)", padding: "var(--spacing-md)" }}>
  ...
</div>
```

```ts
// 타입이 필요하면
import type { ColorToken } from "@aip/design-system";
```

### apps/bff, apps/api

사용하지 않는다. 서버 전용 앱이다.

## 토큰 네이밍 규칙

CSS 변수는 다음 prefix 체계를 따른다:

| 카테고리 | prefix | 예시 |
|---|---|---|
| 색상 | `--color-*` | `--color-primary`, `--color-bg-surface` |
| 폰트 사이즈 | `--font-size-*` | `--font-size-sm`, `--font-size-lg` |
| 폰트 weight | `--font-weight-*` | `--font-weight-bold` |
| 간격 | `--spacing-*` | `--spacing-xs`, `--spacing-md` |
| 반경 | `--radius-*` | `--radius-sm`, `--radius-full` |
| 그림자 | `--shadow-*` | `--shadow-sm` |
| z-index | `--z-*` | `--z-modal` |

**절대 값(hex, rgb, px, rem) 을 직접 노출하지 않는다.** 모든 스타일 값은 CSS 변수를 거친다.

## 영향 범위 주의

CSS 변수 수정은 **모든 소비 앱에 즉시 반영**된다 (빌드 없음). 따라서:

1. 값 변경 시 → 사용 중인 모든 곳에 영향. 변경 이유를 커밋 메시지에 명시
2. 변수 이름 변경 시 → **breaking change**. 소비 앱의 참조를 함께 업데이트해야 한다
3. 새 토큰 추가 → 안전. 추가 후 점진적으로 소비 앱에 적용

현재 소비 앱: `apps/frontend` 만.

## 디자인 리뷰 기준 (자동 FAIL 트리거)

이 패키지에 의존하는 앱에서 다음 패턴은 자동 리뷰 FAIL이다:

1. 하드코딩 색상 (`#hex`, `rgb()`, `hsl()`) 직접 사용
2. 하드코딩 폰트 사이즈 (`14px`, `1rem`)
3. 하드코딩 간격 (`margin: 8px`)
4. 인터랙티브 요소에 `:focus-visible` 스타일 없음
5. 인터랙티브 요소에 `aria-label` 또는 시맨틱 HTML 미사용

→ 반드시 `var(--color-*)`, `var(--font-size-*)`, `var(--spacing-*)` 사용

## 코딩 컨벤션

- **CSS 변수명**: `kebab-case`, 카테고리 prefix 필수
- **index.ts**: 타입 또는 상수만 export. 런타임 로직 없음
- **커밋**: `feat(design-system):`, `fix(design-system):` 등 conventional commits
- **주석/PR 설명**: 토큰 추가/변경 시 "왜" 설명 필수

## 이 패키지에서 하면 안 되는 것

1. ❌ **React 컴포넌트 추가** — 이 패키지는 토큰 전용. 컴포넌트는 `apps/frontend/components/ui/`에 위치
2. ❌ **빌드 스크립트 추가 (`tsc`, `rollup`, `vite build` 등)** — `main: ./index.ts` 직접 참조 방식 유지
3. ❌ **`dist/` 디렉토리 생성** — 빌드 산출물 금지
4. ❌ **앱별 조건부 토큰** — "apps/frontend에서만 적용되는 값" 같은 분기 금지. 토큰은 전역이다
5. ❌ **하드코딩 값 노출** — `index.ts`에서 `export const PRIMARY = "#1e40af"` 같은 것. CSS 변수를 통한 간접 참조만
6. ❌ **외부 패키지 publish** — `private: true` 유지, npm 레지스트리에 올리지 않는다
7. ❌ **런타임 의존성 추가** — 이 패키지는 의존성 0개가 원칙. devDependencies도 불필요
8. ❌ **앱 코드 import** — 단방향. 이 패키지는 어떤 앱도 참조하지 않는다
9. ❌ **토큰 이름의 의미 변경** (`--color-primary`의 색 변경 등) 시 소비 앱 확인 없이 머지 — 영향 범위 검토 필수
