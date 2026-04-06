interface ChatEmptyStateProps {
  profileName?: string;
}

export function ChatEmptyState({ profileName }: ChatEmptyStateProps) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[var(--color-primary-50)]">
        <svg
          className="h-8 w-8 text-[var(--color-primary-500)]"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
          />
        </svg>
      </div>
      <h2 className="mt-4 text-[var(--font-size-xl)] font-semibold text-[var(--color-neutral-900)]">
        {profileName ? `${profileName}와 대화하기` : 'AI Platform'}
      </h2>
      <p className="mt-2 max-w-sm text-center text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
        메시지를 입력하면 AI가 답변을 생성합니다.
        {!profileName && ' 사이드바에서 Profile을 선택하세요.'}
      </p>
    </div>
  );
}
