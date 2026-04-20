# API Key Management

## 개요 (S2)

BFF Admin 엔드포인트로 API Key 의 발급/수정/폐기/회전을 관리한다.
모든 변경은 `api_key_audit_logs` 에 기록된다.

## 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET    | `/bff/api-keys` | 목록 |
| GET    | `/bff/api-keys/:id` | 상세 |
| POST   | `/bff/api-keys` | 발급 (1회 평문) |
| PATCH  | `/bff/api-keys/:id` | 수정 |
| POST   | `/bff/api-keys/:id/revoke` | 폐기 (soft delete) |
| POST   | `/bff/api-keys/:id/rotate` | 회전 (1 트랜잭션) |
| GET    | `/bff/api-keys/:id/audit?limit=N` | 변경 이력 |

## 발급 플로우

1. Admin UI `/admin/api-keys/new` 에서 폼 작성.
2. POST `/bff/api-keys` → 응답에 `plaintext_key` 포함 (1회).
3. `PlaintextRevealModal` 에서 평문 노출 + 복사.
4. 닫으면 평문 재노출 불가.

## 해싱

- 알고리즘: **SHA-256(hex, 64 chars)**
- DB 컬럼: `api_keys.key_hash VARCHAR(64)`
- 평문 로그 금지.

## Rotate 시맨틱

```
BEGIN;
  UPDATE api_keys SET is_active=false, revoked_at=NOW() WHERE id=:old;
  INSERT INTO api_keys (..., rotated_from_id=:old) RETURNING id;
  INSERT INTO api_key_audit_logs (action='rotate_source', ...);
  INSERT INTO api_key_audit_logs (action='rotate_target', ...);
COMMIT;
```

응답에는 **신규 키의 평문** 포함 (1회만).

## Rate Limit 계산

- `rate_limit_per_min`: 분당 호출 한도
- `rate_limit_per_day`: 일별 호출 한도
- Gateway rate_limiter (PostgreSQL Token Bucket) 이 두 값을 모두 검증

## 권한

- 모든 엔드포인트: `JwtAuthGuard` + `RolesGuard(ADMIN)`
- `actor` 는 JWT sub 로 audit log 에 기록
