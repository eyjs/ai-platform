# ADR-003: DocForge 파싱 위임 아키텍처

## Status
Accepted (2026-05-04)

## Context

ai-platform의 Knowledge Pipeline(C10)은 PDF, CSV, Excel 파일을 파싱하여 마크다운으로 변환한 뒤 청킹-임베딩-벡터DB 적재 흐름을 수행한다.

기존 구조에서는 파싱 로직이 ai-platform 내부에 분산되어 있었다:
- `PdfParser`: Docling + VLM OCR + PyMuPDF 조합
- `csv_parser.py`: csv 표준라이브러리 기반 로컬 파서
- `excel_parser.py`: openpyxl 기반 로컬 파서
- `pdf_analyzer.py`: PyMuPDF로 문서 프로파일링 후 Docling/VLM/PyMuPDF 라우팅

문제점:
1. **의존성 비대화**: Docling, openpyxl, VLM OCR 엔드포인트 등 파싱 전용 의존성이 ai-platform에 축적
2. **중복 유지보수**: DocForge(parser 프로젝트)에 이미 고품질 PDF 파싱(OCR, 표 복원, 신뢰도 측정)이 존재하는데, ai-platform에서도 별도 파싱 로직을 유지
3. **파싱 품질 개선이 분산**: PDF 파싱 개선은 DocForge에서, CSV/Excel은 ai-platform에서 각각 진행해야 함
4. **설정 복잡도**: `enable_docling`, `enable_vlm`, `vlm_ocr_endpoint`, `csv_max_rows`, `excel_max_rows` 등 8개 파싱 관련 설정

## Decision

**ai-platform의 모든 파싱을 DocForge API에 위임한다.**

- DocForge `/v1/parse/sync` 엔드포인트가 PDF, CSV, Excel을 MIME 기반으로 통합 처리
- ai-platform은 `DocForgeClient` (httpx AsyncClient)로 DocForge에 파일을 전송하고 마크다운 응답을 수신
- **예외**: TEXT_ONLY PDF는 로컬 PyMuPDF로 직접 추출 (글자 변형 방지, 네트워크 불필요)
- CSV/Excel 로컬 파서는 삭제, Docling/VLM OCR 참조 제거
- 설정은 `docforge_url`, `docforge_timeout_sec`, `docforge_fallback_enabled` 3개로 단순화

## Consequences

### Positive
- ai-platform 파싱 의존성 대폭 감소 (openpyxl, Docling 참조 제거)
- 파싱 개선을 DocForge 한 곳에서만 수행 (단일 책임)
- 설정 복잡도 감소 (8개 -> 6개, 파싱 전용 3개만)
- 인터페이스 변경 없음 (ParseResult, ParsingProvider 유지)

### Negative
- CSV/Excel 파싱에 DocForge 서비스 가동 필수 (로컬 폴백 없음)
- DocForge 장애 시 PDF TEXT_ONLY 외의 모든 파싱이 중단됨
- 네트워크 왕복 추가 (localhost이므로 실질적 영향 최소)

### Neutral
- TEXT_ONLY PDF 판별은 여전히 ai-platform에서 수행 (PyMuPDF 빠른 스캔)
- `AIP_DOCFORGE_FALLBACK_ENABLED=true` 설정 시 PDF는 PyMuPDF 텍스트 추출로 강등 가능

## Alternatives Considered

1. **DocForge를 라이브러리로 import**: 네트워크 없이 직접 호출. 그러나 DocForge는 Flask 앱이며 별도 프로세스로 운영 중. 라이브러리화는 대규모 리팩토링 필요.
2. **로컬 파서 유지 + DocForge는 PDF만**: CSV/Excel 중복 유지. 이번에 해소하려는 문제를 남김.
3. **모든 PDF도 DocForge 위임**: TEXT_ONLY PDF까지 DocForge에 보내면 단순하지만, 불필요한 네트워크 오버헤드 + PyMuPDF의 정확한 텍스트 추출을 포기하는 셈.
