# 3-서비스 라이브 E2E 하니스 (P3 Step 17)

KMS → ai-platform → docforge seam을 잇는 라이브 E2E + 실패주입 하니스.
**테스트 인프라만. 프로덕션 코드(3 repo 모두) 무변** — seam을 고치지 않고 가시화만 한다.

## 무엇을 검증하나

| 파일 | 내용 |
|------|------|
| `test_kms_to_rag.py` (17a) | 골든패스: KMS 업로드→배치(DB-DAMAGE)→ai-platform `documents` 단일행. created=skip/updated=success. 멱등 재배치. |
| `test_seam_failures.py` (17b) | 실패주입 3종 — 현재 코드에서 **실제로 실패함**을 `xfail(strict=True)` 로 가시화. |

### 실패주입 3종 (xfail = 현재 결함 가시화)
| ID | seam 결함 | 앵커 | green 전환 |
|----|-----------|------|-----------|
| G20 | webhook fire-and-forget — 5xx 시 배치 커밋·RAG 미동기 | KMS `placements.service.ts:357` | Step 18 (Outbox) |
| G21 | docforge 인메모리 큐 — 워커 재시작 시 잡 증발(404)→ParseError, 재큐 없음 | docforge `v1_routes.py:294/420`, ai-platform `docforge_client.py:138` | Step 19 (PG큐) |
| G23 | OCR 가용성 캐시 영구 False — 복구 미반영 | docforge `adapters/apple_vision_remote.py:37-50` | Step 20 (호스트 회복) |

> `xfail(strict=True)`: 우연히 통과(XPASS)하면 테스트가 **실패**한다. 즉 "실제로 빨강"임을 강제한다.
> Step 18/19/20에서 각각 green으로 전환하여 봉합 성공을 객관적으로 증명한다.

## 게이팅 (조용한 통과 금지 — 절대)

| 게이트 | 조건 | 미충족 시 |
|--------|------|-----------|
| `AIP_E2E_LIVE=1` | 라이브 E2E 활성화 | **전체 skip + 사유** (모든 e2e 아이템) |
| 헬스 프리체크 | KMS:3001 / ai-platform:8020 / docforge:5051 핑 | **skip + 어떤 서비스가 다운인지 사유** |
| `AIP_*_DATABASE_URL` | DB 단언 커넥션 | **skip + 사유** |
| `AIP_E2E_LIVE_INJECT=1` | G20 실제 5xx 주입 | **skip + 사유** (계약 mock 부적합한 라이브-only 케이스) |

환경 미가용은 **절대 통과(pass)로 처리하지 않는다.** 항상 명시적 skip + reason.

## 라이브 실행

### 1) 서비스 기동 (핸드오프 §6)
```bash
# KMS / ai-platform / docforge 컨테이너 기동 (각 repo의 기동 절차)
#   KMS         :3001
#   ai-platform :8020
#   docforge    :5051
# 호스트 서비스 (docforge가 host.docker.internal 로 호출)
#   OCR        :5052   (실패주입③ G23 대상)
#   임베딩      :8103
```

### 2) 환경변수
```bash
export AIP_E2E_LIVE=1
export AIP_DATABASE_URL="postgresql://aip:aip_dev@localhost:5434/ai_platform"
# 선택 override (기본값은 conftest.py 참조)
export AIP_E2E_KMS_URL="http://localhost:3001"
export AIP_E2E_AIP_URL="http://localhost:8020"
export AIP_E2E_DOCFORGE_URL="http://localhost:5051"
export AIP_E2E_KMS_JWT_SECRET="dev_jwt_secret_key_32_chars_min"
# G20 라이브 5xx 주입을 시도할 때만:
export AIP_E2E_LIVE_INJECT=1
export AIP_E2E_WEBHOOK_FORCE_5XX=1   # webhook 수신부 5xx 강제 메커니즘 (환경별 구현)
```

### 3) 실행
```bash
cd apps/api
# 골든패스 + 실패주입 전부
pytest tests/e2e -v -rsx

# 골든패스만
pytest tests/e2e/test_kms_to_rag.py -v

# 실패주입만 (xfail 확인)
pytest tests/e2e/test_seam_failures.py -v -rsx
```

### 기대 결과 (라이브 정상)
- 골든패스 3건 → **passed**
- 실패주입 G21/G23 → **xfailed** (현재 코드 결함 확인)
- 실패주입 G20 → `AIP_E2E_LIVE_INJECT` + 5xx 메커니즘 있으면 **xfailed**, 없으면 **skipped**

### AIP_E2E_LIVE 미설정 시
- e2e 전체 **skipped** + 사유. CI 기본 잡은 이 상태로 회귀만 돌린다.

## CI 게이트 등록 (라이브 잡 분리)

기본 CI는 e2e를 skip하고 회귀만 돌린다. 라이브 E2E는 **별도 잡**으로 분리한다
(컨테이너 5종 + 호스트 OCR/임베딩 의존이라 기본 잡에 넣으면 불안정).

### 기본 잡 (회귀 — e2e 자동 skip)
```yaml
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e "apps/api[dev,local]"
      # AIP_E2E_LIVE 미설정 → e2e 전체 skip (조용한 통과 아님: collected/skipped 명시)
      - run: cd apps/api && pytest tests -q
```

### 라이브 잡 (수동/스케줄 트리거, 서비스 기동 후)
```yaml
  e2e-live:
    runs-on: self-hosted   # macOS host OCR:5052 필요 (Apple Vision)
    if: github.event_name == 'workflow_dispatch'   # 수동 트리거
    env:
      AIP_E2E_LIVE: "1"
      AIP_DATABASE_URL: "postgresql://aip:aip_dev@localhost:5434/ai_platform"
    steps:
      - uses: actions/checkout@v4
      - run: docker compose up -d            # KMS/ai-platform/docforge/postgres
      - run: ./scripts/wait-for-health.sh    # :3001 :8020 :5051 헬스 대기
      - run: pip install -e "apps/api[dev,local]"
      - run: cd apps/api && pytest tests/e2e -v -rsx
```

> **수동 적용 필요**: 실제 `.github/workflows/*.yml` 파일은 인프라 권한 밖이라
> 본 하니스에서 직접 생성하지 않는다. 위 스니펫을 워크플로에 반영하면 된다.
> OCR(:5052)이 macOS Apple Vision 의존이므로 라이브 잡은 `self-hosted` (macOS) 러너 필요.

## 한계 (계약 mock 폴백)
- 라이브 환경 미가용 시 G21/G23은 **계약 수준 mock** 으로 seam의 실패 *계약* 을 재현한다
  (실제 워커 재시작/OCR 다운이 아님). 각 테스트 docstring에 한계를 명시했다.
- G20(fire-and-forget 유실)은 계약 mock으로 충실히 재현하기 어려워 라이브 주입 전용으로 두고,
  미가용 시 skip한다.
- 완전한 seam 검증(실제 5xx·재시작·다운 주입)은 라이브 환경에서 `AIP_E2E_LIVE_INJECT=1` 로 수행한다.
