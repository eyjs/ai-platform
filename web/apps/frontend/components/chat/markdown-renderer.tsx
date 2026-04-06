'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { useCallback, useState } from 'react';

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
      rehypePlugins={[rehypeHighlight]}
      components={{
        pre({ children }) {
          return <>{children}</>;
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
