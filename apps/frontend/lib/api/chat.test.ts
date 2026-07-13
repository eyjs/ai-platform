import { describe, it, expect } from 'vitest';
import { parseSSEBuffer } from './chat';

/** sse-starlette 실제 출력 포맷(\r\n 종결) 기준의 증분 파서 테스트 */
describe('parseSSEBuffer', () => {
  it('CRLF 종결 이벤트를 전부 파싱한다 (sse-starlette 기본 포맷)', () => {
    const buffer =
      'event: token\r\ndata: {"delta": "A"}\r\n\r\n' +
      'event: token\r\ndata: {"delta": "B"}\r\n\r\n' +
      'event: done\r\ndata: {"answer": "AB"}\r\n\r\n';
    const { events, rest } = parseSSEBuffer(buffer);
    expect(events).toEqual([
      { event: 'token', data: '{"delta": "A"}' },
      { event: 'token', data: '{"delta": "B"}' },
      { event: 'done', data: '{"answer": "AB"}' },
    ]);
    expect(rest).toBe('');
  });

  it('LF 종결 이벤트도 동일하게 파싱한다', () => {
    const buffer = 'event: token\ndata: {"delta": "A"}\n\n';
    const { events, rest } = parseSSEBuffer(buffer);
    expect(events).toEqual([{ event: 'token', data: '{"delta": "A"}' }]);
    expect(rest).toBe('');
  });

  it('미완결 이벤트는 소비하지 않고 rest로 반환한다 (중복 방출 없음)', () => {
    const chunk1 = 'event: token\r\ndata: {"delta": "A"}\r\n\r\nevent: tok';
    const r1 = parseSSEBuffer(chunk1);
    expect(r1.events).toEqual([{ event: 'token', data: '{"delta": "A"}' }]);
    expect(r1.rest).toBe('event: tok');

    const r2 = parseSSEBuffer(r1.rest + 'en\r\ndata: {"delta": "B"}\r\n\r\n');
    expect(r2.events).toEqual([{ event: 'token', data: '{"delta": "B"}' }]);
    expect(r2.rest).toBe('');
  });

  it('청크 경계가 \\r\\n 중간을 갈라도 이벤트가 깨지지 않는다', () => {
    // 'data: X\r\n\r\n'이 '\r' 뒤에서 갈라지는 경우
    const r1 = parseSSEBuffer('event: token\r\ndata: {"delta": "X"}\r');
    const r2 = parseSSEBuffer(r1.rest + '\n\r\n');
    const all = [...r1.events, ...r2.events];
    expect(all).toEqual([{ event: 'token', data: '{"delta": "X"}' }]);
  });

  it('핑 주석(: ping)은 무시한다', () => {
    const buffer =
      ': ping - 2026-07-13 08:08:15\r\n\r\n' +
      'event: token\r\ndata: {"delta": "A"}\r\n\r\n';
    const { events } = parseSSEBuffer(buffer);
    expect(events).toEqual([{ event: 'token', data: '{"delta": "A"}' }]);
  });

  it('event 없는 data는 message 이벤트로 처리한다', () => {
    const { events } = parseSSEBuffer('data: hello\r\n\r\n');
    expect(events).toEqual([{ event: 'message', data: 'hello' }]);
  });

  it('여러 data 줄은 \\n으로 결합한다', () => {
    const { events } = parseSSEBuffer(
      'event: token\r\ndata: line1\r\ndata: line2\r\n\r\n',
    );
    expect(events).toEqual([{ event: 'token', data: 'line1\nline2' }]);
  });

  it('완전한 스트림을 1바이트씩 흘려도 전체 이벤트가 정확히 1회씩 나온다', () => {
    const stream =
      'event: token\r\ndata: {"delta": "가"}\r\n\r\n' +
      ': ping\r\n\r\n' +
      'event: done\r\ndata: {"answer": "가"}\r\n\r\n';
    let buffer = '';
    const collected: Array<{ event: string; data: string }> = [];
    for (const ch of stream) {
      buffer += ch;
      const { events, rest } = parseSSEBuffer(buffer);
      collected.push(...events);
      buffer = rest;
    }
    expect(collected).toEqual([
      { event: 'token', data: '{"delta": "가"}' },
      { event: 'done', data: '{"answer": "가"}' },
    ]);
  });
});
