# LLM 모델 토폴로지 — 투트랙 라이트사이징 (2026-07-08)

## 원칙

작업 난이도에 맞춰 모델을 라이트사이징한다. **생성만 대형 LLM**, 오케스트레이션은 경량.

| 트랙 | 작업 | 권장 크기 | 이유 |
|------|------|-----------|------|
| **분류 (router_llm)** | 의도분류, mode 선택, 의미분류 (`ai_router.py` IntentClassifier/ModeSelector/SemanticClassifier), faithfulness | **1.7B** | 제약된 구조적 판정 — 소형이 잘함 |
| **오케스트레이션 (orchestration_llm)** | 쿼리확장(expand_queries), 쿼리재작성(rewrite_query), 계획수립(planner) | **4B** | 생성적이지만 짧고 구조적 — 중경량 충분 |
| **생성 (main_llm)** | 답변 생성(generate/regenerate) | **9B+** | 품질 직결 — 대형 유지 |
| **사주 리포트 (report_llm)** | SajuReport* (별도 제품) | 14B | ⚠️ RAG 무관, 사주 전용 |

## 코드 배선 (구현됨)

- `factory.get_orchestration_llm()` — `orchestrator_server_url`(미설정 시 `router_llm_server_url` 폴백)
- `planner`/`rewrite_query` → `orchestration_llm` (`build_deterministic_graph(orchestration_llm=...)`)
- `expand_queries` → `orchestration_llm` (`RAGSearchTool(router_llm=orchestration_llm)`)
- 분류(IntentClassifier/ModeSelector/SemanticClassifier/Faithfulness) → `router_llm` (그대로)
- 생성 → `main_llm` (그대로)

> **하위호환**: `orchestrator_server_url` 미설정이면 오케스트레이션이 router로 폴백 →
> 현재 동작과 동일. 아래 env 를 설정하고 모델 서버를 띄우면 투트랙이 활성화된다.

## 활성화 (사용자 인프라 작업)

### 1. env (ai-platform)
```
ROUTER_LLM_SERVER_URL=http://host.docker.internal:<1.7B_PORT>   # 분류 → 1.7B
ORCHESTRATOR_SERVER_URL=http://host.docker.internal:<4B_PORT>   # 계획/재작성/확장 → 4B
MAIN_LLM_SERVER_URL=http://host.docker.internal:8106            # 생성 → 9B (기존)
```

### 2. 모델 서버 (host launchd, MLX)
- 신규: 1.7B(예: `mlx-community/Qwen3-1.7B-4bit`), 4B(`mlx-community/Qwen3-4B-4bit`) MLX 서버 기동
  (`com.kms.mlx-classifier.plist`, `com.kms.mlx-orchestrator.plist` 등)
- 기존 `com.kms.mlx-router.plist`(8B, 8105) → 중단 가능 (1.7B/4B가 대체)

### 3. 메모리 영향 (4bit 대략)
| 구성 | 모델 | 합계 |
|------|------|------|
| 현재 | 8B + 9B + 14B | ~17.5GB |
| 사주 유지 | 1.7B + 4B + 9B + **14B** | ~16.5GB (절감 미미 — 14B가 지배) |
| 사주 제외 | 1.7B + 4B + 9B | **~8.5GB (절반)** |

## ⚠️ 사주 리포트(14B) 결정 필요

`report_llm`(14B, 8104, `com.joonbi.mlx-saju.plist`)은 **사주 리포트 전용**이다
(`SajuReportPaperTool`, `SajuReportCompatibilityTool`, `SajuReportService`; saju-backend/db/redis 가동 중).
RAG와 무관하지만 **제거하면 사주 리포트 생성이 깨진다.**

- **사주 유지**: 14B 존치 → 메모리 절감은 미미(~1GB). 라이트사이징은 속도 이득 위주.
- **사주 제외**(별도 제품 종료 시): 14B 제거 → 메모리 절반. **명시적 제품 결정 필요.**

## 검증 포인트 (활성화 후)

- 라우팅 품질: 1.7B가 needs_rag/mode 를 오분류하지 않는지 (오분류 시 RAG 품질 하락).
- 확장/재작성 품질: 4B 쿼리 변형이 recall 을 유지하는지.
- 트레이스 패널(요청로그)에서 단계별 지연이 실제로 줄었는지 확인.
