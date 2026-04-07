'use client';

import { Suspense, useState, type FormEvent } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { useAuth } from '@/lib/auth/auth-context';

function LoginForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const { login } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  const callbackUrl = searchParams.get('callbackUrl') || '/';

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');

    if (!email) {
      setError('이메일을 입력하세요');
      return;
    }
    if (!password) {
      setError('비밀번호를 입력하세요');
      return;
    }

    setLoading(true);
    try {
      await login(email, password);
      router.push(callbackUrl);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : '로그인에 실패했습니다',
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="w-full max-w-[400px] p-8">
      <div className="mb-8 text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-[var(--radius-lg)] bg-[var(--color-primary-50)]">
          <svg
            className="h-6 w-6 text-[var(--color-primary-500)]"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M13 10V3L4 14h7v7l9-11h-7z"
            />
          </svg>
        </div>
        <h1 className="text-[var(--font-size-2xl)] font-bold text-[var(--color-neutral-900)]">
          AI Platform
        </h1>
        <p className="mt-1 text-[var(--font-size-sm)] text-[var(--color-neutral-500)]">
          계정으로 로그인하세요
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-[var(--radius-md)] bg-[var(--color-error-light)] border border-[var(--color-error)] px-4 py-3 text-[var(--font-size-sm)] text-[var(--color-error)]">
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Input
          label="이메일"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="admin@ai-platform.local"
          autoComplete="email"
          autoFocus
        />
        <Input
          label="비밀번호"
          type={showPassword ? 'text' : 'password'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="비밀번호를 입력하세요"
          autoComplete="current-password"
          rightIcon={
            <button
              type="button"
              onClick={() => setShowPassword(!showPassword)}
              className="text-[var(--color-neutral-400)] hover:text-[var(--color-neutral-600)]"
              tabIndex={-1}
            >
              {showPassword ? '숨김' : '보기'}
            </button>
          }
        />
        <Button
          type="submit"
          variant="primary"
          size="lg"
          fullWidth
          loading={loading}
          className="mt-2"
        >
          로그인
        </Button>
      </form>
    </Card>
  );
}

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--surface-page)] px-4">
      <Suspense fallback={<div className="text-[var(--color-neutral-400)]">로딩 중...</div>}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
