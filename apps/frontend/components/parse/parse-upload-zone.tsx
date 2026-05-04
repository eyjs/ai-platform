'use client';

import { useCallback, useRef, useState, type DragEvent } from 'react';
import { cn } from '@/lib/cn';
import { Button } from '@/components/ui/button';

const MAX_FILE_SIZE_MB = 100;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;
const ACCEPTED_MIME = 'application/pdf';

interface ParseUploadZoneProps {
  onFileSelect: (file: File) => void;
  isUploading: boolean;
}

export function ParseUploadZone({
  onFileSelect,
  isUploading,
}: ParseUploadZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const validateFile = useCallback((file: File): string | null => {
    if (file.type !== ACCEPTED_MIME) {
      return 'PDF 파일만 업로드할 수 있습니다.';
    }
    if (file.size > MAX_FILE_SIZE_BYTES) {
      return `파일 크기가 ${MAX_FILE_SIZE_MB}MB를 초과합니다.`;
    }
    return null;
  }, []);

  const handleFile = useCallback(
    (file: File) => {
      const error = validateFile(file);
      if (error) {
        setValidationError(error);
        return;
      }
      setValidationError(null);
      onFileSelect(file);
    },
    [validateFile, onFileSelect],
  );

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);

      const file = e.dataTransfer.files[0];
      if (file) {
        handleFile(file);
      }
    },
    [handleFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        handleFile(file);
      }
      // 동일 파일 재선택 허용
      if (inputRef.current) {
        inputRef.current.value = '';
      }
    },
    [handleFile],
  );

  const handleButtonClick = useCallback(() => {
    inputRef.current?.click();
  }, []);

  return (
    <div className="flex flex-col items-center gap-[var(--spacing-4)]">
      <div
        role="button"
        tabIndex={0}
        aria-label="PDF 파일을 드래그하여 업로드하거나 클릭하여 선택"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={handleButtonClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleButtonClick();
          }
        }}
        className={cn(
          'flex w-full max-w-xl flex-col items-center justify-center gap-[var(--spacing-4)]',
          'rounded-[var(--radius-xl)] border-2 border-dashed p-[var(--spacing-12)]',
          'transition-colors cursor-pointer',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary-200)] focus-visible:ring-offset-2',
          isDragOver
            ? 'border-[var(--color-primary-500)] bg-[var(--color-primary-50)]'
            : 'border-[var(--color-neutral-300)] bg-[var(--surface-card)] hover:border-[var(--color-primary-400)] hover:bg-[var(--color-neutral-50)]',
          isUploading && 'pointer-events-none opacity-60',
        )}
      >
        {/* Upload Icon */}
        <div
          className={cn(
            'flex h-16 w-16 items-center justify-center rounded-full',
            isDragOver
              ? 'bg-[var(--color-primary-100)]'
              : 'bg-[var(--color-neutral-100)]',
          )}
        >
          <svg
            className={cn(
              'h-8 w-8',
              isDragOver
                ? 'text-[var(--color-primary-500)]'
                : 'text-[var(--color-neutral-400)]',
            )}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={1.5}
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"
            />
          </svg>
        </div>

        <div className="text-center">
          <p className="text-[var(--font-size-base)] font-medium text-[var(--color-neutral-800)]">
            {isDragOver
              ? 'PDF 파일을 놓아주세요'
              : 'PDF 파일을 드래그하거나 클릭하여 선택'}
          </p>
          <p className="mt-[var(--spacing-1)] text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
            최대 {MAX_FILE_SIZE_MB}MB, PDF 형식만 지원
          </p>
        </div>

        {isUploading && (
          <div className="flex items-center gap-[var(--spacing-2)]">
            <svg
              className="h-5 w-5 animate-spin text-[var(--color-primary-500)]"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
              />
            </svg>
            <span className="text-[var(--font-size-sm)] font-medium text-[var(--color-primary-600)]">
              파싱 중... (최대 2분 소요)
            </span>
          </div>
        )}
      </div>

      {validationError && (
        <p
          role="alert"
          className="text-[var(--font-size-sm)] text-[var(--color-error)]"
        >
          {validationError}
        </p>
      )}

      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        onChange={handleInputChange}
        className="hidden"
        aria-hidden="true"
        tabIndex={-1}
      />

      <Button
        variant="secondary"
        size="lg"
        onClick={handleButtonClick}
        disabled={isUploading}
        aria-label="PDF 파일 선택"
      >
        파일 선택
      </Button>
    </div>
  );
}
