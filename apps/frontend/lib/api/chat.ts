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

export function parseSSEEvents(chunk: string): ParsedSSEEvent[] {
  const events: ParsedSSEEvent[] = [];
  const lines = chunk.split('\n');
  let currentEvent = '';
  let currentData = '';

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      currentEvent = line.slice(7).trim();
    } else if (line.startsWith('data: ')) {
      currentData = line.slice(6);
    } else if (line === '' && (currentEvent || currentData)) {
      events.push({
        event: currentEvent || 'message',
        data: currentData,
      });
      currentEvent = '';
      currentData = '';
    }
  }

  return events;
}
