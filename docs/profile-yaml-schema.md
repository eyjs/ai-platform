# Profile YAML Schema

정본: `.pipeline/contracts/profile-yaml-schema.json` (JSON Schema draft-07).
BFF 복사본: `apps/bff/src/profiles/schema/profile-schema.json` (런타임 로딩).

## 필수 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | string | `^[a-z0-9][a-z0-9_-]{1,63}$` |
| `name` | string | 1~255 chars |
| `mode` | enum | `deterministic | agentic | workflow` |

## 주요 블록

### `providers` (S6)
```yaml
providers:
  candidates:
    - provider_id: ollama
      priority: 1
    - provider_id: anthropic_claude
      priority: 10
      require_tool_use: true
      max_cost_per_1k: 0.01
  fallback_on: [timeout, 5xx, unavailable]
  max_fallback_depth: 2
```

### `cache` (S5)
```yaml
cache:
  enabled: true
  ttl_seconds: 3600
  agentic_enabled: false   # agentic 모드 캐시 opt-in
```

### `guardrails`
```yaml
guardrails:
  faithfulness_threshold: 0.8
  pii_masking: true
```

## 에러 메시지 (한국어)

- `required.id`: "프로필 id 는 필수입니다."
- `required.name`: "프로필 name 은 필수입니다."
- `required.mode`: "mode 는 필수입니다 (deterministic | agentic | workflow)."
- `pattern.id`: "id 형식이 올바르지 않습니다."

## 저장 흐름

1. BFF 가 YAML → JSON 파싱 → ajv 로 JSON Schema 검증
2. 실패 시 400 + `errors: [...]`
3. 성공 시 `agent_profiles.config` 업데이트
4. `NOTIFY profile_updated, '<profile_id>'` 발행
5. api 의 `profile_store` 가 LISTEN 수신 → in-memory reload + response_cache 무효화
