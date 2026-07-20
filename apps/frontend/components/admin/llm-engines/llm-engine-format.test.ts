import { describe, it, expect } from 'vitest';
import {
  checkedAtToDate,
  describeFallback,
  formatContextLength,
  formatLatency,
  formatRelativeCheckedAt,
  hasToolsCapability,
  toLatencyLevel,
  toLinkState,
} from './llm-engine-format';

describe('toLinkState', () => {
  it('true -> up, false -> down', () => {
    expect(toLinkState(true)).toBe('up');
    expect(toLinkState(false)).toBe('down');
  });

  it('null은 unknown이다 — down으로 접히면 정상 엔진을 장애로 오보한다', () => {
    expect(toLinkState(null)).toBe('unknown');
    expect(toLinkState(null)).not.toBe('down');
  });
});

describe('checkedAtToDate', () => {
  it('UNIX 초를 밀리초로 변환한다', () => {
    // 1784173221.5초 -> 1784173221500ms
    expect(checkedAtToDate(1784173221.5)?.getTime()).toBe(1784173221500);
  });

  it('초를 밀리초로 오인하지 않는다 (1970년으로 찍히면 안 됨)', () => {
    const date = checkedAtToDate(1784173221.5);
    expect(date?.getUTCFullYear()).toBeGreaterThan(2020);
  });

  it('null/비유한값은 null', () => {
    expect(checkedAtToDate(null)).toBeNull();
    expect(checkedAtToDate(Number.NaN)).toBeNull();
  });
});

describe('formatRelativeCheckedAt', () => {
  const nowSeconds = 1784173221.5;
  const nowMs = nowSeconds * 1000;

  it('초 단위 경과를 표시한다', () => {
    expect(formatRelativeCheckedAt(nowSeconds - 3, nowMs)).toBe('3초 전 확인');
    expect(formatRelativeCheckedAt(nowSeconds, nowMs)).toBe('0초 전 확인');
  });

  it('분/시간/일 단위로 접는다', () => {
    expect(formatRelativeCheckedAt(nowSeconds - 90, nowMs)).toBe('1분 전 확인');
    expect(formatRelativeCheckedAt(nowSeconds - 7200, nowMs)).toBe('2시간 전 확인');
    expect(formatRelativeCheckedAt(nowSeconds - 172800, nowMs)).toBe('2일 전 확인');
  });

  it('시계 오차로 미래여도 음수를 노출하지 않는다', () => {
    expect(formatRelativeCheckedAt(nowSeconds + 5, nowMs)).toBe('방금 확인');
  });

  it('checkedAt이 없으면 문구를 지어내지 않는다', () => {
    expect(formatRelativeCheckedAt(null, nowMs)).toBeNull();
  });
});

describe('formatContextLength', () => {
  it('262144 -> 262K', () => {
    expect(formatContextLength(262144)).toBe('262K');
  });

  it('1048576 -> 1M', () => {
    expect(formatContextLength(1048576)).toBe('1M');
  });

  it('1000 미만은 그대로', () => {
    expect(formatContextLength(512)).toBe('512');
  });

  it('없으면 대시', () => {
    expect(formatContextLength(null)).toBe('-');
  });
});

describe('hasToolsCapability', () => {
  it('tools 유무를 판별한다', () => {
    expect(hasToolsCapability(['vision', 'completion', 'tools', 'thinking'])).toBe(true);
    expect(hasToolsCapability(['vision', 'completion'])).toBe(false);
    expect(hasToolsCapability([])).toBe(false);
  });
});

describe('describeFallback', () => {
  it('development면 MLX로 폴백한다고 말한다', () => {
    expect(describeFallback('development', true)).toContain('MLX');
  });

  it('anthropic이면 Claude로 폴백한다고 말한다', () => {
    expect(describeFallback('anthropic', true)).toContain('Claude');
  });

  it('폴백이 꺼져 있으면 대체 경로가 없음을 경고한다', () => {
    const text = describeFallback('development', false);
    expect(text).toContain('중단');
    expect(text).not.toContain('MLX');
  });

  it('알 수 없는 모드는 모드명을 그대로 노출한다', () => {
    expect(describeFallback('openai', true)).toContain('openai');
  });
});

describe('부하 관측: latency', () => {
  it('응답시간을 부하 등급으로 매핑한다', () => {
    expect(toLatencyLevel(400)).toBe('fast');
    expect(toLatencyLevel(3000)).toBe('warn');
    expect(toLatencyLevel(18000)).toBe('slow');
  });

  it('미측정(null)은 등급을 매기지 않는다 — 0으로 접으면 "빠름"으로 오독한다', () => {
    expect(toLatencyLevel(null)).toBeNull();
  });

  it('사람이 읽는 응답시간으로 포맷한다', () => {
    expect(formatLatency(400)).toBe('400ms');
    expect(formatLatency(18059)).toBe('18.1초');
    expect(formatLatency(null)).toBeNull();
  });
});
