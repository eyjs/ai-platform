'use client';

import { useRef, useState, useCallback, useEffect } from 'react';

export function useScrollAnchor() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [showScrollButton, setShowScrollButton] = useState(false);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  const handleScroll = useCallback(() => {
    const container = scrollRef.current;
    if (!container) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    const atBottom = scrollHeight - scrollTop - clientHeight < 100;
    setIsAtBottom(atBottom);
    setShowScrollButton(!atBottom);
  }, []);

  // 새 콘텐츠 추가 시 하단에 있으면 자동 스크롤
  useEffect(() => {
    if (isAtBottom) {
      bottomRef.current?.scrollIntoView({ behavior: 'instant' });
    }
  });

  return {
    scrollRef,
    bottomRef,
    isAtBottom,
    showScrollButton,
    scrollToBottom,
    handleScroll,
  };
}
