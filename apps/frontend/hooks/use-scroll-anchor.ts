'use client';

import { useRef, useState, useCallback, useEffect } from 'react';

/**
 * 채팅 메시지 목록 오토스크롤 앵커.
 *
 * @param dep 콘텐츠 변경 신호(보통 messages). 이 값이 바뀔 때만 하단 자동 스크롤한다.
 *   (의존성 없이 매 렌더 실행하면 사용자가 위로 스크롤해도 즉시 하단으로 스냅되어 스크롤 불가)
 */
export function useScrollAnchor(dep?: unknown) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // isAtBottom은 ref로 — 매 렌더 effect 의존성/피드백 루프를 피하고 항상 최신값 사용.
  const isAtBottomRef = useRef(true);
  const [showScrollButton, setShowScrollButton] = useState(false);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  const handleScroll = useCallback(() => {
    const container = scrollRef.current;
    if (!container) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    const atBottom = scrollHeight - scrollTop - clientHeight < 100;
    isAtBottomRef.current = atBottom;
    setShowScrollButton(!atBottom);
  }, []);

  // 새 콘텐츠(dep) 추가 시, 사용자가 하단에 있을 때만 자동 스크롤.
  // dep 변경 시에만 실행 → 사용자가 위로 스크롤한 상태(isAtBottomRef=false)면 건드리지 않음.
  useEffect(() => {
    if (isAtBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'instant' });
    }
  }, [dep]);

  return {
    scrollRef,
    bottomRef,
    showScrollButton,
    scrollToBottom,
    handleScroll,
  };
}
