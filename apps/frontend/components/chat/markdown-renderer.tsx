'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import rehypeHighlight from 'rehype-highlight';
import { useCallback, useState } from 'react';

// LLM 답변의 인라인 HTML(<br> 등, 특히 표 셀 안 줄바꿈)을 렌더링하되
// sanitize로 스크립트·이벤트 핸들러류는 차단한다.
// code의 className은 rehype-highlight 언어 감지에 필요해 허용 목록에 추가.
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code ?? []), ['className', /^language-/]],
  },
} as typeof defaultSchema;

interface MarkdownRendererProps {
  content: string;
}

function CodeBlock({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const language = className?.replace('language-', '') || '';

  const handleCopy = useCallback(async () => {
    const text = String(children).replace(/\n$/, '');
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [children]);

  return (
    <div className="group relative my-2">
      <div className="flex items-center justify-between rounded-t-[var(--radius-md)] bg-[var(--color-neutral-800)] px-3 py-1.5">
        <span className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
          {language || 'code'}
        </span>
        <button
          onClick={handleCopy}
          className="text-[var(--font-size-xs)] text-[var(--color-neutral-400)] hover:text-white transition-colors"
        >
          {copied ? '복사됨' : '복사'}
        </button>
      </div>
      <pre className="!mt-0 !rounded-t-none">
        <code className={className}>{children}</code>
      </pre>
    </div>
  );
}

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  if (!content) return null;

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema], rehypeHighlight]}
      components={{
        pre({ children }) {
          return <>{children}</>;
        },
        a({ href, children }) {
          // LLM이 간혹 만드는 가짜 링크(http://문서:... 등)는 클릭하면 404가
          // 되므로 일반 텍스트로 무해화. 유효한 http(s) 링크만 링크로 렌더
          // (밑줄로 클릭 가능함을 명확히, 새 탭).
          let valid = false;
          try {
            const url = new URL(href ?? '', 'http://invalid.local');
            valid =
              (url.protocol === 'http:' || url.protocol === 'https:') &&
              url.hostname.includes('.') &&
              Boolean(href?.startsWith('http'));
          } catch {
            valid = false;
          }
          if (!valid) return <span>{children}</span>;
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--color-primary-700)] underline underline-offset-2 hover:text-[var(--color-primary-800)]"
            >
              {children}
            </a>
          );
        },
        code({ className, children, ...props }) {
          const isInline = !className;
          if (isInline) {
            return (
              <code
                className="rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-1.5 py-0.5 text-[var(--font-size-sm)] font-mono text-[var(--color-primary-700)]"
                {...props}
              >
                {children}
              </code>
            );
          }
          return <CodeBlock className={className}>{children}</CodeBlock>;
        },
        table({ children }) {
          return (
            <div className="my-2 overflow-x-auto">
              <table className="w-full border-collapse border border-[var(--color-neutral-200)] text-[var(--font-size-sm)]">
                {children}
              </table>
            </div>
          );
        },
        th({ children }) {
          return (
            <th className="border border-[var(--color-neutral-200)] bg-[var(--color-neutral-50)] px-3 py-2 text-left font-medium">
              {children}
            </th>
          );
        },
        td({ children }) {
          return (
            <td className="border border-[var(--color-neutral-200)] px-3 py-2">
              {children}
            </td>
          );
        },
        blockquote({ children }) {
          return (
            <blockquote className="my-2 border-l-4 border-[var(--color-primary-300)] bg-[var(--color-primary-50)] py-2 pl-4 pr-2 text-[var(--color-neutral-700)]">
              {children}
            </blockquote>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
