'use client';

import { useMemo } from 'react';

interface YamlDiffViewerProps {
  previousYaml: string;
  currentYaml: string;
  className?: string;
}

interface DiffLine {
  type: 'unchanged' | 'added' | 'removed';
  content: string;
  lineNumber?: number;
}

export function YamlDiffViewer({
  previousYaml,
  currentYaml,
  className
}: YamlDiffViewerProps) {
  const diffResult = useMemo(() => {
    return computeLineDiff(previousYaml, currentYaml);
  }, [previousYaml, currentYaml]);

  return (
    <div className={className}>
      <div className="grid grid-cols-2 gap-4 h-full">
        {/* Previous Version */}
        <div className="border border-[var(--color-neutral-200)] rounded-[var(--radius-md)] overflow-hidden">
          <div className="bg-[var(--color-neutral-50)] px-3 py-2 border-b border-[var(--color-neutral-200)]">
            <h4 className="text-[var(--font-size-sm)] font-semibold text-[var(--color-neutral-700)]">
              이전 버전
            </h4>
          </div>
          <div className="h-[400px] overflow-y-auto font-mono text-[var(--font-size-sm)]">
            {diffResult.previous.map((line, index) => (
              <div
                key={index}
                className={`flex ${getLineClassNames(line.type)}`}
              >
                <span className="w-12 px-2 py-1 text-[var(--color-neutral-400)] text-right border-r border-[var(--color-neutral-200)] bg-[var(--color-neutral-25)] shrink-0">
                  {line.lineNumber || ''}
                </span>
                <span className="px-3 py-1 flex-1 whitespace-pre-wrap break-all">
                  {line.content || ' '}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Current Version */}
        <div className="border border-[var(--color-neutral-200)] rounded-[var(--radius-md)] overflow-hidden">
          <div className="bg-[var(--color-neutral-50)] px-3 py-2 border-b border-[var(--color-neutral-200)]">
            <h4 className="text-[var(--font-size-sm)] font-semibold text-[var(--color-neutral-700)]">
              현재 버전
            </h4>
          </div>
          <div className="h-[400px] overflow-y-auto font-mono text-[var(--font-size-sm)]">
            {diffResult.current.map((line, index) => (
              <div
                key={index}
                className={`flex ${getLineClassNames(line.type)}`}
              >
                <span className="w-12 px-2 py-1 text-[var(--color-neutral-400)] text-right border-r border-[var(--color-neutral-200)] bg-[var(--color-neutral-25)] shrink-0">
                  {line.lineNumber || ''}
                </span>
                <span className="px-3 py-1 flex-1 whitespace-pre-wrap break-all">
                  {line.content || ' '}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function getLineClassNames(type: DiffLine['type']): string {
  switch (type) {
    case 'added':
      return 'bg-[var(--color-success-light)]';
    case 'removed':
      return 'bg-[var(--color-error-light)]';
    case 'unchanged':
    default:
      return 'bg-[var(--surface-card)]';
  }
}

function computeLineDiff(previous: string, current: string): {
  previous: DiffLine[];
  current: DiffLine[];
} {
  const prevLines = previous.split('\n');
  const currLines = current.split('\n');

  // Simple LCS-based diff implementation
  const lcs = longestCommonSubsequence(prevLines, currLines);

  const previousResult: DiffLine[] = [];
  const currentResult: DiffLine[] = [];

  let prevIndex = 0;
  let currIndex = 0;
  let lcsIndex = 0;

  while (prevIndex < prevLines.length || currIndex < currLines.length) {
    if (lcsIndex < lcs.length &&
        prevIndex < prevLines.length &&
        currIndex < currLines.length &&
        prevLines[prevIndex] === currLines[currIndex] &&
        prevLines[prevIndex] === lcs[lcsIndex]) {

      // Unchanged line
      previousResult.push({
        type: 'unchanged',
        content: prevLines[prevIndex],
        lineNumber: prevIndex + 1,
      });
      currentResult.push({
        type: 'unchanged',
        content: currLines[currIndex],
        lineNumber: currIndex + 1,
      });

      prevIndex++;
      currIndex++;
      lcsIndex++;
    } else if (prevIndex < prevLines.length &&
               (lcsIndex >= lcs.length ||
                prevLines[prevIndex] !== lcs[lcsIndex])) {

      // Removed line
      previousResult.push({
        type: 'removed',
        content: prevLines[prevIndex],
        lineNumber: prevIndex + 1,
      });

      prevIndex++;
    } else if (currIndex < currLines.length &&
               (lcsIndex >= lcs.length ||
                currLines[currIndex] !== lcs[lcsIndex])) {

      // Added line
      currentResult.push({
        type: 'added',
        content: currLines[currIndex],
        lineNumber: currIndex + 1,
      });

      currIndex++;
    }
  }

  return {
    previous: previousResult,
    current: currentResult,
  };
}

function longestCommonSubsequence(a: string[], b: string[]): string[] {
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0));

  // Build DP table
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack to find the LCS
  const result: string[] = [];
  let i = m;
  let j = n;

  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      result.unshift(a[i - 1]);
      i--;
      j--;
    } else if (dp[i - 1][j] > dp[i][j - 1]) {
      i--;
    } else {
      j--;
    }
  }

  return result;
}