'use client';

import { useCallback, useState } from 'react';
import { ParseUploadZone } from '@/components/parse/parse-upload-zone';
import { ParseResultViewer } from '@/components/parse/parse-result-viewer';
import { ParseDownloadBar } from '@/components/parse/parse-download-bar';
import { ParseErrorState } from '@/components/parse/parse-error-state';
import { uploadPdf } from '@/lib/parse-api';
import type { ParseError, ParseResultData, ParseStatus } from '@/types/parse';

export default function ParsePage() {
  const [status, setStatus] = useState<ParseStatus>('idle');
  const [result, setResult] = useState<ParseResultData | null>(null);
  const [markdown, setMarkdown] = useState('');
  const [error, setError] = useState<ParseError | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);

  const handleFileSelect = useCallback(async (file: File) => {
    setPdfFile(file);
    setStatus('uploading');
    setError(null);
    setResult(null);

    try {
      setStatus('parsing');
      const response = await uploadPdf(file);
      setResult(response.data);
      setMarkdown(response.data.markdown);
      setStatus('done');
    } catch (err) {
      const message = err instanceof Error ? err.message : '알 수 없는 오류가 발생했습니다.';
      setError({
        code: 'UPLOAD_FAILED',
        message,
      });
      setStatus('error');
    }
  }, []);

  const handleRetry = useCallback(() => {
    if (pdfFile) {
      handleFileSelect(pdfFile);
    } else {
      setStatus('idle');
      setError(null);
    }
  }, [pdfFile, handleFileSelect]);

  const handleReset = useCallback(() => {
    setStatus('idle');
    setResult(null);
    setMarkdown('');
    setError(null);
    setPdfFile(null);
  }, []);

  return (
    <div className="flex flex-1 flex-col">
      {/* Upload / Error State */}
      {(status === 'idle' || status === 'uploading' || status === 'parsing') && (
        <div className="flex flex-1 flex-col items-center justify-center p-[var(--spacing-6)]">
          <div className="mb-[var(--spacing-8)] text-center">
            <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
              PDF Parser
            </h1>
            <p className="mt-[var(--spacing-2)] text-[var(--font-size-base)] text-[var(--color-neutral-500)]">
              PDF 문서를 업로드하면 마크다운으로 변환합니다
            </p>
          </div>
          <ParseUploadZone
            onFileSelect={handleFileSelect}
            isUploading={status === 'uploading' || status === 'parsing'}
          />
        </div>
      )}

      {status === 'error' && error && (
        <div className="flex flex-1 flex-col items-center justify-center p-[var(--spacing-6)]">
          <ParseErrorState error={error} onRetry={handleRetry} />
        </div>
      )}

      {/* Result State */}
      {status === 'done' && result && (
        <div className="flex flex-1 flex-col gap-[var(--spacing-3)] p-[var(--spacing-4)]">
          <ParseDownloadBar
            markdown={markdown}
            originalFileName={pdfFile?.name || 'document.pdf'}
            stats={result.stats}
            onReset={handleReset}
          />
          <div className="flex-1" style={{ minHeight: 0 }}>
            <ParseResultViewer
              markdown={markdown}
              onMarkdownChange={setMarkdown}
              pdfFile={pdfFile}
            />
          </div>
        </div>
      )}
    </div>
  );
}
