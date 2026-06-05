# ADR-008: docforge 호스트 엔진 가용성 자가회복 — TTL 재프로브 (G23, Seam③)

## Status
Accepted (2026-06-05)

## Context

docforge(문서 파싱/OCR 서비스, 별도 `parser` 레포)가 Docker 컨테이너에서 실행될 때, macOS 호스트가 HTTP로 노출하는 외부 엔진을 호출한다:

- Apple Vision OCR — `http://host.docker.internal:5052`
- Qwen2-VL — `http://host.docker.internal:5053`
- (관측 대상) 임베딩 서비스 — `:8103`

두 원격 어댑터(`apple_vision_remote.py`, `host_vlm_engine.py`)는 `is_available()` 결과를 `self._available: bool | None`에 **영구 캐시**했다:

```python
def is_available(self) -> bool:
    if self._available is not None:   # 한 번 평가하면 영구 반환
        return self._available
    ...
    except Exception:
        self._available = False        # 다운 시 영구 False 고착
    return self._available
```

결함(P3 Step 17 실패주입③, G23으로 가시화): 호스트 서비스가 한 번이라도 다운이면 가용성 캐시가 **영구 False**가 된다. 호스트 OCR/VLM이 재기동돼도 docforge는 그 사실을 영원히 모르고 죽은 채 유지된다 — **자가회복 불가.** graceful degrade(빈 결과)는 동작하지만 회복 경로가 없어, docforge 프로세스 자체를 재시작해야만 복구됐다. Step 17b가 `test_g23_ocr_availability_cache_sticky`(`@pytest.mark.xfail(strict=True)`)로 못 박았다.

## Decision

### 1. 영구 캐시 → TTL 재프로브 (자가회복)

`self._available` 영구 캐시를 공통 헬퍼 `host_health.TTLAvailability`로 교체한다:

- `time.monotonic()` 기준 마지막 프로브 시각을 기록.
- TTL(기본 30s, `DOCFORGE_HOST_PROBE_TTL_SEC`) 안에서는 캐시 반환(불필요한 프로브 폭주 방지).
- TTL 경과 후 다음 `is_available()`에서 health를 **재프로브** → 현재 상태 반영.
- 원격 호출(`recognize`/`correct_page`/`describe_image`) 실패 시 `invalidate()`로 다음 호출에서 즉시 재프로브(호스트가 사용 중 죽은 경우 빠른 회복).

재기동된 호스트 서비스를 docforge가 **스스로 다시 잡는다.** 소비처(`adaptive_retry.py`, `_parse_pdf_helpers.py`)가 매 페이지/매 잡에서 `is_available()`을 평가하므로, TTL 경과 직후의 첫 호출에서 자동 정상화된다.

### 2. 경량 백그라운드 헬스 폴러 (관측)

`host_health.HostHealthPoller` — stdlib daemon thread로 OCR/VLM/EMB의 `/health`를 주기(기본 15s, `DOCFORGE_HEALTH_POLL_SEC`) 핑하고 **상태 전이(up↔down)만** 로깅(노이즈 억제). 관측 전용이며 **자동 시작하지 않는다**(명시 `start()` 시에만) → 기존 동작 무변·회귀 0.

### 3. health 스키마 차이 흡수

OCR/VLM은 `{"status":"ok"}`, 임베딩(:8103)은 `{"status":"healthy"}`를 반환한다. `probe_health`는 둘 다 healthy로 판정(`status in ("ok","healthy")`)해 하나의 프로브로 모든 호스트를 다룬다.

### 4. 자동기동은 범위 외 (문서화로 대체)

다운된 호스트 서비스를 docforge가 **자동 기동**하는 것은 범위 외다(권한·라이프사이클 경계). docforge는 재기동을 *스스로 감지*해 다시 잡을 뿐, 서비스 기동은 운영자/launchd 책임이다. 수동 검증 절차는 릴리즈 노트·status.json에 명시.

## Consequences

### 긍정
- 호스트 서비스 재기동 후 docforge 프로세스 재시작 없이 **자가회복**. 회복 지연은 TTL(기본 30s, 호출 실패 시 즉시) 1회뿐.
- graceful degrade 보존 — 다운 동안 빈 결과, 회복 시 자동 정상화.
- 추가 인프라·의존성 0(stdlib urllib/json/threading만). docforge 단일 컨테이너 정신 유지.
- 두 어댑터 중복 제거(공통 `TTLAvailability`/`probe_health`).

### 한계 / 비용
- TTL 경과 직후 첫 호출이 health 프로브 1회를 추가 부담(수 ms, timeout 3~5s 상한).
- 헬스 폴러는 자동 시작하지 않으므로 가시성을 원하면 부팅 훅에서 명시 `start()` 필요.
- 컨테이너 내부(`host.docker.internal`)에서의 다운→재기동 자동회복 실증은 docforge 컨테이너를 이번 머지로 재빌드한 뒤에만 라이브로 관측 가능(수동 게이트). 단 코드 동작은 **호스트 직접 라이브 검증 완료**(실 어댑터·실 :5052/:5053·실 wall-clock TTL로 다운→재프로브→True 자가회복 실증).

## Verification

- parser 신규 단위 `tests/unit/test_host_health.py` 16건: TTL 만료 재프로브, 캐시 유지, 실패→복구 전이, `invalidate` 즉시 재프로브, 폴러 전이 로깅, `probe_health` 스키마(ok·healthy), 어댑터 자가회복(통합). 어댑터 인접 회귀 40 passed.
- ai-platform `test_g23_ocr_availability_reprobe_recovery`: `xfail(strict)` 제거, "재기동 후 재프로브 자가회복" green 단언. 회복이 재프로브에서 비롯됨을 `probe_count`로 증명(가짜 통과 방지).
- **라이브 자가회복 실증**: 실 `AppleVisionRemoteEngine`을 다운 타깃→캐시 False→복구돼도 TTL 내 False(캐시 증명)→실 1.2s sleep으로 TTL 경과→재프로브→True + `host engine recovered (re-probe)` 로깅.

## Related
- ADR-006 (Outbox, Seam① G20), ADR-007 (docforge 내구 큐, Seam② G21). 본 ADR은 Seam③(G23) 봉합으로 P3 안전망의 외부 의존 회복 경로를 완성.
- parser merge `63dff7b` (feat `1c14b63`), ai-platform merge (test `98041b1`).
