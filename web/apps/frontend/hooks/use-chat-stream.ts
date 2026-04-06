'use client';

import { useState, useRef, useCallback } from 'react';
import { streamChat, parseSSEEvents } from '@/lib/api/chat';
import type { ChatRequest } from '@/types/chat';

interface UseChatStreamCallbacks {
  onToken: (text: string) => void;
  onReplace: (text: string) => void;
  onTrace: (data: Record<string, unknown>) => void;
  onDone: (data: { sources?: Array<{ title: string; url?: string }> }) => void;
  onError: (error: Error) => void;
}

export function useChatStream(callbacks: UseChatStreamCallbacks) {
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (request: ChatRequest, token: string) => {
      setIsStreaming(true);
      const abortController = new AbortController();
      abortControllerRef.current = abortController;

      try {
        const stream = streamChat(request, token, abortController.signal);
        const reader = stream.getReader();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += value;
          const events = parseSSEEvents(buffer);

          // 마지막 불완전한 이벤트를 버퍼에 유지
          const lastNewline = buffer.lastIndexOf('\n\n');
          if (lastNewline >= 0) {
            buffer = buffer.slice(lastNewline + 2);
          }

          for (const event of events) {
            try {
              const data = JSON.parse(event.data);
              switch (event.event) {
                case 'token':
                  callbacks.onToken(data.text || data);
                  break;
                case 'replace':
                  callbacks.onReplace(data.text || data);
                  break;
                case 'trace':
                  callbacks.onTrace(data);
                  break;
                case 'done':
                  callbacks.onDone(data);
                  break;
                case 'error':
                  callbacks.onError(new Error(data.message || 'SSE 에러'));
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
      } catch (error) {
        if (
          error instanceof DOMException &&
          error.name === 'AbortError'
        ) {
          // 사용자가 중단함 — 정상
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
