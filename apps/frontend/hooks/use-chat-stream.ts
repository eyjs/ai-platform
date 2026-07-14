'use client';

import { useState, useRef, useCallback } from 'react';
import { streamChat, parseSSEBuffer } from '@/lib/api/chat';
import type { ChatRequest } from '@/types/chat';

interface UseChatStreamCallbacks {
  onToken: (text: string) => void;
  onReplace: (text: string) => void;
  onTrace: (data: Record<string, unknown>) => void;
  onDone: (data: {
    /** api 가 생성한 최종 답변 전문. 토큰 누적이 누락돼도 이 값으로 보정. */
    answer?: string;
    sources?: Array<{ title: string; document_id?: string; url?: string }>;
    /** api 가 생성한 응답 식별자. 피드백 전송 시 사용. */
    response_id?: string;
  }) => void;
  onError: (error: Error) => void;
  /**
   * done 이벤트 없이 스트림이 끝났을 때 (사용자 중단 포함).
   * 말풍선이 스트리밍 상태로 영구 고착되지 않도록 호출자가 마감 처리한다.
   */
  onIncomplete: (reason: 'aborted' | 'no_done') => void;
  /** 401/403 — 호출자가 토큰 갱신 후 재시도하거나 로그인으로 보낸다. */
  onAuthError: () => void;
}

export function useChatStream(callbacks: UseChatStreamCallbacks) {
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (request: ChatRequest, token: string) => {
      setIsStreaming(true);
      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      // done/error/auth_error 중 하나라도 받으면 종단 처리됨 — onIncomplete로 덮지 않는다
      let sawTerminal = false;
      try {
        const stream = streamChat(request, token, abortController.signal);
        const reader = stream.getReader();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += value;
          // 완결된 이벤트만 소비하고 미완결 꼬리는 버퍼에 유지 (중복 방출 없음)
          const { events, rest } = parseSSEBuffer(buffer);
          buffer = rest;

          for (const event of events) {
            try {
              const data = JSON.parse(event.data);
              switch (event.event) {
                case 'token':
                  callbacks.onToken(data.delta || data.text || String(data));
                  break;
                case 'replace':
                  callbacks.onReplace(data.delta || data.text || String(data));
                  break;
                case 'trace':
                  callbacks.onTrace(data);
                  break;
                case 'done':
                  sawTerminal = true;
                  callbacks.onDone(data);
                  break;
                case 'error':
                  sawTerminal = true;
                  callbacks.onError(new Error(data.message || 'SSE 에러'));
                  break;
                case 'auth_error':
                  sawTerminal = true;
                  callbacks.onAuthError();
                  break;
              }
            } catch {
              // JSON 파싱 실패 시 문자열로 처리
              if (event.event === 'token') {
                callbacks.onToken(event.data);
              }
            }
          }
        }

        if (!sawTerminal) {
          // 종단 이벤트 없이 연결이 끝남(중단/유실) — 고착 방지 마감
          callbacks.onIncomplete(
            abortController.signal.aborted ? 'aborted' : 'no_done',
          );
        }
      } catch (error) {
        if (
          error instanceof DOMException &&
          error.name === 'AbortError'
        ) {
          // 사용자가 중단함 — 말풍선은 마감 처리
          callbacks.onIncomplete('aborted');
        } else {
          callbacks.onError(
            error instanceof Error ? error : new Error('스트리밍 실패'),
          );
        }
      } finally {
        setIsStreaming(false);
        abortControllerRef.current = null;
      }
    },
    [callbacks],
  );

  const abort = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  return { sendMessage, isStreaming, abort };
}
