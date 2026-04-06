'use client';

import { useState, useCallback, useEffect } from 'react';

export function useUnsavedChanges() {
  const [hasChanges, setHasChanges] = useState(false);
  const [originalContent, setOriginalContent] = useState('');

  const setOriginal = useCallback((content: string) => {
    setOriginalContent(content);
    setHasChanges(false);
  }, []);

  const checkChanges = useCallback(
    (currentContent: string) => {
      setHasChanges(currentContent !== originalContent);
    },
    [originalContent],
  );

  const markSaved = useCallback(() => {
    setHasChanges(false);
  }, []);

  // 이탈 경고
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasChanges) {
        e.preventDefault();
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasChanges]);

  return { hasChanges, setOriginal, checkChanges, markSaved };
}
