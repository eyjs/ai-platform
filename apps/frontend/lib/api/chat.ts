import type { ChatRequest, ChatProfileOption } from '@/types/chat';

const FASTAPI_URL =
  process.env.NEXT_PUBLIC_FASTAPI_URL || 'http://localhost:8000';

/** FastAPI GET /api/profiles — 채팅 화면 Profile 선택용 */
export async function fetchProfiles(): Promise<ChatProfileOption[]> {
  const res = await fetch(`${FASTAPI_URL}/api/profiles`);
  if (!res.ok) throw new Error('Profile 목록을 불러올 수 없습니다');
  return res.json();
}

/** FastAPI POST /api/chat/stream — SSE 스트리밍 */
export function streamChat(
  request: ChatRequest,
  token: string,
  signal?: AbortSignal,
): ReadableStream<string> {
  return new ReadableStream({
    async start(controller) {
      try {
        const res = await fetch(`${FASTAPI_URL}/api/chat/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(request),
          signal,
        });

        if (!res.ok) {
          const errorText = await res.text();
          controller.enqueue(
            `event: error\ndata: ${JSON.stringify({ message: errorText })}\n\n`,
          );
          controller.close();
          return;
        }

        const reader = res.body?.getReader();
        if (!reader) {
          controller.close();
          return;
        }

        const decoder = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          controller.enqueue(decoder.decode(value, { stream: true }));
        }
        controller.close();
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          controller.close();
          return;
        }
        controller.error(error);
      }
    },
  });
}

/** SSE 이벤트 파서 */
export interface ParsedSSEEvent {
  event: string;
  data: string;
}

/**
 * 버퍼에서 완결된 SSE 이벤트만 소비하고 나머지를 반환하는 증분 파서.
 *
 * 줄 종결은 \r\n / \n / \r 모두 허용한다 — sse-starlette는 \r\n을 쓰므로
 * \n 전용 파싱은 이벤트 경계(빈 줄)를 영원히 못 만난다. 이벤트는 빈 줄에서만
 * 확정·소비되므로, 반환된 rest를 다음 청크 앞에 이어붙이면 중복 방출이 없다.
 */
export function parseSSEBuffer(buffer: string): {
  events: ParsedSSEEvent[];
  rest: string;
} {
  const events: ParsedSSEEvent[] = [];
  let currentEvent = '';
  let dataLines: string[] = [];
  let consumed = 0;

  const lineRe = /(.*?)(\r\n|\r|\n)/g;
  let match: RegExpExecArray | null;
  while ((match = lineRe.exec(buffer)) !== null) {
    const line = match[1];
    if (line === '') {
      // 빈 줄 = 이벤트 경계. 여기까지를 소비 확정한다.
      if (currentEvent || dataLines.length > 0) {
        events.push({
          event: currentEvent || 'message',
          data: dataLines.join('\n'),
        });
      }
      currentEvent = '';
      dataLines = [];
      consumed = lineRe.lastIndex;
    } else if (line.startsWith('event:')) {
      currentEvent = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''));
    }
    // ':' 주석(핑 등)·기타 필드는 무시
  }

  return { events, rest: buffer.slice(consumed) };
}
