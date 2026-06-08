# ADR-011: KMS 회사도메인 → ai-platform 상품도메인 매핑 — 웹훅 categoryPath 계약 + 설정형 해석

## Status
Accepted (2026-06-08)

## Context

KMS(원문/배치 SoT)와 ai-platform(RAG/챗봇)은 **도메인을 다른 축으로 분류**한다:

- **KMS = 회사중심 도메인.** 문서를 회사코드(예: `DB-DAMAGE`)로 배치한다. 그 아래
  카테고리 트리(자동차보험→개인용/업무용, 장기보험→건강/간병/상해/…, 일반보험→화재/종합)가 붙는다.
- **ai-platform = 상품중심 도메인.** 챗봇 프로필 `domain_scopes`(자동차보험·건강보험·간병보험·
  실손보험·화재보험)로 RAG 검색 스코프를 건다.

동기화 경로(`KMS placements.create → outbox document.updated → ai-platform kms_sync.sync_document
→ ingest → vector_store`)에서 aip-pg는 KMS 회사도메인(`DB-DAMAGE`)을 **그대로** `documents`·
`document_chunks.domain_code`에 적재했다. 그 결과 상품도메인 스코프(`자동차보험` 등)를 거는 프로필이
DB손해보험 문서를 **검색 대상에서 배제**했다. 라이브에서 크롤한 DB손해보험 자동차보험 6건이 챗봇에 안 떠,
aip-pg `domain_code`를 수동으로 `DB-DAMAGE`→`자동차보험`으로 패치했으나 **재동기화하면 되돌아가는
데이터 패치**라 근본해결이 필요했다.

근본 원인은 둘이다:

1. **웹훅 payload에 카테고리가 없다.** `document.updated` payload = `{documentId, domainCodes:["DB-DAMAGE"]}`
   뿐이었다. 매핑에 필요한 카테고리경로가 ai-platform에 도달하지 않았다. (KMS는 docCode 생성을 위해
   `placements.service.calculateCategoryPath`로 `["DB-DAMAGE","자동차보험","개인용"]`을 *이미 계산*하고
   있었으나 웹훅으로 전달만 안 했다.)
2. **두 분류가 이질적이다(1:1 규칙 없음).** `자동차보험`(top)은 상품도메인 직결이지만 `장기보험/건강`→
   `건강보험`, `장기보험/간병`→`간병보험`, `일반보험/화재`→`화재보험`처럼 비균일하고, 일부(종합/상해/
   질병/연금/재물/운전자)는 대응 상품도메인이 없다. 컨벤션으로 환원 불가 → **명시적 매핑**이 필요하다.

ADR-009는 "RAG 데이터의 SoT = ai-platform"을 확정했다. 본 결정은 그 위에서 **분류의 SoT는 KMS**임을
명시하고, ai-platform이 KMS 분류를 *해석*하는 레이어 계약을 정의한다.

## Decision

### 1. 분류 레이어 계약: KMS = 분류 SoT, ai-platform = 해석

- KMS는 회사도메인·카테고리 트리의 SoT다. KMS의 분류 체계나 `calculateCategoryPath` 로직은 변경하지
  않는다(재사용만). KMS 분류를 ai-platform 쪽 상품도메인으로 *바꾸는* 책임은 **전적으로 ai-platform**에 있다.
- ai-platform은 KMS 분류를 **해석(interpret)**만 한다. KMS는 ai-platform/상품도메인의 존재를 알지 않는다
  (역방향 의존 금지 — 루트 경계 규칙·ADR-009와 일관).

### 2. 전달 계약: 웹훅 payload에 `categoryPath: string[]` 추가

`document.updated` payload를 확장한다(하위호환 — 신규 옵셔널 필드):

```json
{
  "documentId": "0bcea…",
  "domainCodes": ["DB-DAMAGE"],
  "categoryPath": ["DB-DAMAGE", "자동차보험", "개인용"]
}
```

- `categoryPath[0]` = 회사도메인코드, `[1]` = 최상위 카테고리(상품유형군), 이후 하위.
- KMS `placements.service`가 docCode용 `calculateCategoryPath`를 재사용해 계산한다.
- **계산은 트랜잭션 밖, enqueue는 트랜잭션 안.** 카테고리 조회 I/O를 outbox 트랜잭션 밖에서 수행해
  인터랙티브 트랜잭션 창을 넓히지 않는다(G20/ADR-006 — I/O를 트랜잭션 밖으로). `enqueue(tx, …)` 자체는
  기존 `$transaction` 안에 그대로 두어 배치/docCode/이력과 **원자적**으로 커밋된다(G20 원자성 보존).
- 대안(거부): `categoryId`만 보내고 ai-platform이 KMS에 역질의 → 동기 RTT·결합 증가. KMS가 경로를
  이미 계산하므로 payload에 싣는 편이 단순하고 결합이 낮다.

### 3. 해석 계약: 설정형 매핑(`seeds/domain_mapping.yaml`)

ai-platform이 `(회사도메인, categoryPath) → 상품도메인`을 **설정 파일**로 해석한다:

```yaml
DB-DAMAGE:
  "자동차보험": 자동차보험        # top 직결(개인용/업무용/영업용 모두)
  "장기보험/건강": 건강보험
  "장기보험/간병": 간병보험
  "일반보험/화재": 화재보험
```

- 매핑 키 = `categoryPath[1:]`을 `/`로 결합한 경로. `resolve_product_domain`이 **가장 구체적(긴 경로)부터**
  매칭하고, 미매핑/부재 시 `None`을 반환한다.
- 대안(거부): DB 테이블 → 마이그레이션 부담·코드리뷰 가시성 저하. 설정 YAML은 코드리뷰 가능하고 새 상품/
  카테고리 추가가 파일 한 줄(운영성, 코드 변경 0).

### 4. 조용한 누락 0: 미매핑은 fallback + WARN

매핑이 없거나 categoryPath가 부재(구 KMS·타 consumer·매핑 미정의 카테고리/도메인 예: HANHWA)면
**회사도메인을 그대로 적재(fallback)**하되 반드시 `kms_sync_domain_unmapped` WARN으로 가시화한다.
챗봇 비노출이 곧 미매핑 신호이므로, 누락을 조용히 통과시키지 않는다.

## Consequences

### 좋은 점
- DB손해보험 자동차보험 문서를 **재동기화해도** aip-pg `domain_code=자동차보험`으로 적재 → 수동 패치 불필요,
  챗봇이 출처로 사용. 장기보험/건강 → 건강보험으로 적재.
- 분류 책임이 명확히 분리된다(KMS=SoT, ai-platform=해석). 새 매핑은 YAML 한 줄.
- 하위호환: categoryPath 없는 구 payload·타 consumer는 기존 동작(도메인 그대로 + WARN). G20 원자성·디스패처 불변.

### 비용/주의
- **cross-repo 계약**: `categoryPath` 필드명·형태가 양 repo에서 일치해야 한다(본 ADR로 고정).
- **배포 결합(수동 게이트)**: KMS(kms-api)와 ai-platform(worker)을 **함께** 재배포해야 계약이 성립한다.
  KMS만 재배포하면 ai-platform이 옛 코드로 categoryPath를 무시(여전히 fallback). 본 변경은 현재 가동
  컨테이너 **미반영** — 재배포는 수동 게이트.
- **기존 데이터 backfill(수동 게이트)**: 이미 적재된 DB손해보험 문서(수동 패치 6건 포함)는 재동기화
  (placement touch 또는 재sync 트리거)해야 매핑 경로로 재적재된다. 멱등(external_id UPSERT).
- 매핑 미정의 카테고리(종합/상해/질병 등)는 의도적으로 fallback — WARN 메트릭으로 추적해 필요 시 매핑 추가.

## Verification
- ai-platform: 매핑 단위테스트 16건(자동차보험/장기보험·건강/간병/구체성우선/미매핑 None/빈배열/도메인코드만/
  미정의 도메인 + kms_sync 적용·fallback·WARN). 전체 단위 1041 passed/9 skipped/0 fail(`--ignore=tests/e2e`).
- KMS: `npx tsc --noEmit` green. 신규 placements 단위테스트(경로 계산/폴백, payload categoryPath 적재·동일 tx)
  + outbox(G20) 10건 포함 api 스위트 42 passed.
- 계약 정합: KMS payload `categoryPath` ↔ ai-platform `data.get("categoryPath")` 소비 일치 확인.

## Related
- ADR-006: Transactional Outbox — webhook 발행 내구화(G20). 본 변경은 그 트랜잭션 원자성을 보존한다.
- ADR-009: RAG 단일 진실원천 = ai-platform. 본 변경은 "분류 SoT = KMS, 해석 = ai-platform"을 명시해 그 경계를 보완한다.
