/**
 * SSE trace 이벤트 → 사용자에게 보여줄 진행 상태 문구.
 *
 * 챗봇 말풍선이 답변 시작 전 "무엇을 하고 있는지"를 회색 글씨로 고지한다.
 * null 반환 = 상태 갱신 없음 (내부 관측용 이벤트).
 */
export function traceToStatus(data: Record<string, unknown>): string | null {
  const step = data.step as string | undefined;
  const status = data.status as string | undefined;
  const tool = data.tool as string | undefined;

  // 도구 실행 결과 (개별 도구)
  if (tool === 'rag_search') {
    const found = data.chunks_found;
    return typeof found === 'number' && found > 0
      ? `관련 문서 ${found}개 구간을 찾았습니다. 내용을 검토하는 중...`
      : null;
  }
  if (tool) return null; // fact_lookup 등 내부 도구는 표시 생략

  switch (step) {
    case 'supervisor':
      return status === 'start' ? '요청을 분석하는 중...' : null;
    case 'planning':
      return '어떤 자료를 찾을지 계획하는 중...';
    case 'tool_execution':
      if (status === 'start') return '관련 문서를 검색하는 중...';
      if (status === 'end') {
        const results = data.results;
        return typeof results === 'number' && results > 0
          ? `자료 ${results}건 검토 완료. 답변을 준비하는 중...`
          : '검색 완료. 답변을 준비하는 중...';
      }
      return null;
    case 'graph_enrich':
      return '연관 문서를 함께 확인하는 중...';
    case 'rewrite_query':
      return '검색어를 바꿔 다시 검색하는 중...';
    case 'widen_retry':
      return '원하는 정보를 찾지 못해 검색 범위를 넓혀 다시 확인하는 중...';
    case 'generation':
      return status === 'start' ? '답변을 작성하는 중...' : null;
    case 'guardrail':
      return status === 'start' ? '답변의 근거를 검증하는 중...' : null;
    default:
      return null;
  }
}
