# Provider Routing Policy (S6)

Profile YAML `providers:` 블록으로 런타임 provider 선택 정책을 표현한다.
Gateway 는 policy + 현재 registry 의 교집합을 계산하여 primary 와 fallback chain 을 결정한다.

## 순수함수 설계

```
parse_policy(profile.config)
  → ProviderPolicy(candidates, fallback_on, max_fallback_depth)
select_primary(policy, available)
  → ProviderCapability | None
select_fallback_chain(policy, available, exclude_ids)
  → list[ProviderCapability]  (최대 max_fallback_depth)
```

## 평가 규칙

1. `candidates` 는 `priority` 오름차순 (숫자가 작을수록 우선).
2. 각 candidate 조건 필터:
   - `require_tool_use=true` 인데 `capability.supports_tool_use=false` → 제외
   - `require_streaming=true` 인데 `supports_streaming=false` → 제외
   - `max_cost_per_1k` 초과 → 제외
3. 첫 매칭을 primary 로 선택.
4. primary 를 제외한 나머지 중 최대 `max_fallback_depth` 개를 chain 으로 구성 (최대 2).

## 실패 처리

`invoke_with_fallback(call_fn)`:
1. primary 호출 → 예외 시 warning log
2. chain 순회 시도 → 모두 실패 시 `ProviderUnavailableError` 전파
3. Gateway 는 이 에러를 502 `provider_unavailable` 로 매핑

## YAML 예시

```yaml
id: customer-support
name: 고객지원 챗봇
mode: agentic
providers:
  candidates:
    - provider_id: anthropic_claude
      priority: 1
      require_tool_use: true
    - provider_id: openai
      priority: 2
      require_tool_use: true
      max_cost_per_1k: 0.02
    - provider_id: ollama
      priority: 99
  fallback_on: [timeout, 5xx, unavailable]
  max_fallback_depth: 2
```

- 개발 단계: registry 에 `ollama` 만 활성 → priority 99 가 primary.
- 상용 전환: `anthropic_claude.stub=false` 로 전환 → priority 1 로 동작.
