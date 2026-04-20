# Provider Switching Guide

이 문서는 AI Platform 의 Provider 패턴 확장 결과(S1) 에 대한 운영 가이드다.

## 개요

`apps/api` 는 `LLMProvider` 추상화를 통해 로컬/상용 LLM 을 런타임에 교체 가능하다.

## Capability Matrix

| provider_id | tool_use | streaming | max_context | cost/1k | stub |
|-------------|:-------:|:---------:|------------:|--------:|:----:|
| `ollama`           | ✗ | ✓ | 8192   | 0.000 | ✗ |
| `http_llm`         | ✗ | ✓ | 8192   | 0.000 | ✗ |
| `openai`           | ✓ | ✓ | 128000 | 0.010 | ✗ |
| `anthropic_claude` | ✓ | ✓ | 200000 | 0.008 | **✓ (개발)** |

## 전환 방법

### 로컬 개발 (기본)
```bash
export AIP_PROVIDER_MODE=development
# Ollama 가 http://localhost:11434 에 올라가 있어야 함
```

### HTTP LLM (MLX 서버)
```bash
export AIP_PROVIDER_MODE=development
export AIP_MAIN_LLM_SERVER_URL=http://localhost:8080
# HttpLLMProvider 로 자동 전환
```

### OpenAI (상용 전환 시)
```bash
export AIP_PROVIDER_MODE=production
export AIP_OPENAI_API_KEY=sk-REPLACE_ME
export AIP_PROVIDER_ENABLE_OPENAI=1
```

### Anthropic Claude (현재 Stub)
- `AIP_PROVIDER_ENABLE_ANTHROPIC=1` 로 registry 에 등록만 가능.
- 실 호출은 SDK 통합 후 활성. 개발 단계에서는 `AIP_PROVIDER_ANTHROPIC_STUB_MODE=echo` 로 placeholder 응답 테스트 가능.

## 상용 전환 체크리스트

- [ ] 실 SDK 의존성 추가 (`pip install anthropic`)
- [ ] `AnthropicStubProvider` 를 실 구현으로 교체 (`capability.stub=False`)
- [ ] `.env` 에 API Key 설정 (커밋 금지)
- [ ] 비용 모니터링 alert 설정
- [ ] Rate Limit 정책 재검토
- [ ] 프로파일의 `providers.candidates` 에 `anthropic_claude` 추가
