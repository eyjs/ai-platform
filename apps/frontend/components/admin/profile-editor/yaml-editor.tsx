'use client';

import dynamic from 'next/dynamic';
import { useCallback, useRef, useEffect } from 'react';

const MonacoEditor = dynamic(() => import('@monaco-editor/react'), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center bg-[var(--surface-editor)]">
      <span className="text-[var(--color-neutral-400)]">에디터 로딩 중...</span>
    </div>
  ),
});

interface YamlEditorProps {
  value: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  className?: string;
}

export function YamlEditor({ value, onChange, onSave, className }: YamlEditorProps) {
  const editorRef = useRef<unknown>(null);

  const handleEditorMount = useCallback(
    (editor: unknown, monaco: unknown) => {
      editorRef.current = editor;

      // Ctrl+S / Cmd+S 저장 단축키
      const ed = editor as { addCommand: (keybinding: number, handler: () => void) => void };
      const m = monaco as { KeyMod: { CtrlCmd: number }; KeyCode: { KeyS: number } };
      ed.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyS, () => {
        onSave?.();
      });
    },
    [onSave],
  );

  return (
    <div className={className}>
      <MonacoEditor
        height="100%"
        language="yaml"
        theme="vs-dark"
        value={value}
        onChange={(val) => onChange(val || '')}
        onMount={handleEditorMount}
        options={{
          minimap: { enabled: false },
          fontSize: 14,
          fontFamily: 'var(--font-mono)',
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          tabSize: 2,
          insertSpaces: true,
          automaticLayout: true,
          bracketPairColorization: { enabled: true },
          guides: { indentation: true },
          renderLineHighlight: 'all',
          scrollbar: {
            verticalScrollbarSize: 8,
            horizontalScrollbarSize: 8,
          },
        }}
      />
    </div>
  );
}
