import { describe, it, expect } from 'vitest';
import { traceToStatus } from './trace-status';

describe('traceToStatus', () => {
  it('supervisor 시작 → 분석 중', () => {
    expect(traceToStatus({ step: 'supervisor', status: 'start' })).toBe(
      '요청을 분석하는 중...',
    );
  });

  it('도구 실행 시작/종료', () => {
    expect(traceToStatus({ step: 'tool_execution', status: 'start' })).toBe(
      '관련 문서를 검색하는 중...',
    );
    expect(
      traceToStatus({ step: 'tool_execution', status: 'end', results: 8 }),
    ).toBe('자료 8건 검토 완료. 답변을 준비하는 중...');
  });

  it('rag_search 결과는 발견 개수를 알린다', () => {
    expect(traceToStatus({ tool: 'rag_search', chunks_found: 5 })).toContain(
      '5개 구간',
    );
    expect(traceToStatus({ tool: 'rag_search', chunks_found: 0 })).toBeNull();
  });

  it('내부 도구(fact_lookup)는 표시하지 않는다', () => {
    expect(traceToStatus({ tool: 'fact_lookup', chunks_found: 0 })).toBeNull();
  });

  it('확장 재시도는 사용자에게 이유를 설명한다', () => {
    expect(traceToStatus({ step: 'widen_retry', status: 'start' })).toBe(
      '원하는 정보를 찾지 못해 검색 범위를 넓혀 다시 확인하는 중...',
    );
  });

  it('생성 시작·가드레일 시작을 알린다', () => {
    expect(traceToStatus({ step: 'generation', status: 'start' })).toBe(
      '답변을 작성하는 중...',
    );
    expect(traceToStatus({ step: 'guardrail', status: 'start' })).toBe(
      '답변의 근거를 검증하는 중...',
    );
  });

  it('내부 관측 이벤트는 상태를 갱신하지 않는다', () => {
    expect(traceToStatus({ step: 'evaluate_results' })).toBeNull();
    expect(traceToStatus({ step: 'generation', status: 'end' })).toBeNull();
    expect(traceToStatus({ step: 'guardrail_modified' })).toBeNull();
    expect(traceToStatus({})).toBeNull();
  });
});
