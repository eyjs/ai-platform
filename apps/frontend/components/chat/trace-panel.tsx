'use client';

import { useState } from 'react';
import { formatDuration, latencyColor } from '@/lib/format';

/**
 * 답변별 RAG 파이프라인 트레이스.
 * SSE trace 이벤트(실시간) 또는 저장된 latency_breakdown 노드를 단계로 펼쳐 본다.
 * rag_search 단계는 필터 기준·리랭킹 입력 후보·최종 청크(스니펫)까지 상세 표시.
 */

type Chunk = {
  chunk_id?: string;
  document_id?: string;
  score?: number;
  snippet?: string;
};

type TraceDetail = {
  filter?: Record<string, unknown>;
  expanded_queries?: string[];
  candidates?: Chunk[];
  reranked?: Chunk[];
  reranked_by?: string;
};

function shortId(id?: string): string {
  if (!id) return '—';
  return id.length > 8 ? id.slice(0, 8) : id;
}

/** 청크 목록 (점수 내림차순 표시, 스크롤). rerank 입력/출력 공용. */
function ChunkList({ title, chunks }: { title: string; chunks: Chunk[] }) {
  if (!chunks || chunks.length === 0) return null;
  return (
    <div className="mt-1.5">
      <div className="mb-1 text-[var(--font-size-xs)] font-semibold text-[var(--color-neutral-600)]">
        {title} ({chunks.length})
      </div>
      <ol className="max-h-56 space-y-1 overflow-y-auto pr-1">
        {chunks.map((c, i) => (
          <li
            key={c.chunk_id ?? i}
            className="rounded-[var(--radius-sm)] bg-[var(--color-neutral-50)] px-2 py-1 text-[var(--font-size-xs)]"
          >
            <div className="flex items-center gap-2">
              <span className="w-4 shrink-0 text-right text-[var(--color-neutral-400)]">{i + 1}</span>
              <span className="font-mono text-[var(--color-neutral-500)]">doc {shortId(c.document_id)}</span>
              <span className="font-mono text-[var(--color-neutral-400)]">#{shortId(c.chunk_id)}</span>
              {typeof c.score === 'number' && (
                <span className="ml-auto shrink-0 font-mono text-[var(--color-primary)]">
                  {c.score.toFixed(4)}
                </span>
              )}
            </div>
            {c.snippet && (
              <p className="mt-0.5 pl-6 text-[var(--color-neutral-500)] line-clamp-2">{c.snippet}</p>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** rag_search 상세: 필터 기준 + 확장쿼리 + 리랭킹 입력/최종 청크 */
function RagSearchDetail({ detail }: { detail: TraceDetail }) {
  const filter = detail.filter ?? {};
  const domains = filter.domain_codes as string[] | null | undefined;
  return (
    <div className="mt-1 border-l-2 border-[var(--color-neutral-200)] pl-3">
      {/* 메타데이터 필터 기준 */}
      <div className="text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
        <span className="font-semibold">필터 기준:</span>{' '}
        도메인 {domains && domains.length ? domains.join(', ') : '전체'}
        {filter.security_level_max ? ` · 보안 ≤ ${String(filter.security_level_max)}` : ''}
        {filter.tenant_id ? ` · 테넌트 ${String(filter.tenant_id)}` : ''}
        {filter.allowed_doc_ids ? ` · 허용문서 ${String(filter.allowed_doc_ids)}건` : ''}
      </div>
      {/* 확장 쿼리 */}
      {detail.expanded_queries && detail.expanded_queries.length > 0 && (
        <div className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
          <span className="font-semibold">확장 쿼리:</span>
          <ul className="mt-0.5 space-y-0.5 pl-3">
            {detail.expanded_queries.map((q, i) => (
              <li key={i} className="text-[var(--color-neutral-500)]">
                {i === 0 ? '· ' : '↳ '}
                {q}
              </li>
            ))}
          </ul>
        </div>
      )}
      {/* 리랭킹 입력 후보 (무엇을 기반으로 리랭킹했나) */}
      <ChunkList
        title={`리랭킹 입력 후보${detail.reranked_by ? ` · ${detail.reranked_by}` : ''}`}
        chunks={detail.candidates ?? []}
      />
      {/* 최종 채택 청크 (어떤 청크를 잡았나) */}
      <ChunkList title="최종 채택 청크" chunks={detail.reranked ?? []} />
      {(!detail.candidates || detail.candidates.length === 0) && (
        <div className="mt-1 text-[var(--font-size-xs)] text-[var(--color-neutral-400)]">
          검색 결과 없음 (매칭 청크 0건)
        </div>
      )}
    </div>
  );
}

/** generation 상세: 첫토큰 지연 + 생성속도 */
function GenerationDetail({ e }: { e: Record<string, unknown> }) {
  const ttft = typeof e.ttft_ms === 'number' ? e.ttft_ms : null;
  const rate = typeof e.chunks_per_s === 'number' ? e.chunks_per_s : null;
  const ctx = typeof e.context_chunks === 'number' ? e.context_chunks : null;
  const chunks = typeof e.chunks === 'number' ? e.chunks : null;
  return (
    <div className="mt-1 border-l-2 border-[var(--color-neutral-200)] pl-3 text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
      {ttft != null && <span>첫토큰 {(ttft / 1000).toFixed(1)}s</span>}
      {rate != null && <span> · {rate.toFixed(0)} tok/s</span>}
      {chunks != null && <span> · {chunks}청크 생성</span>}
      {ctx != null && <span> · 컨텍스트 {ctx}청크</span>}
    </div>
  );
}

/**
 * 단계 식별 정규화 키. 실시간 이벤트({tool/step})와 저장된 노드({node:"tool:rag_search"})를
 * 동일하게 취급하기 위해 "tool:" 접두어를 제거한다. → 채팅/요청로그 패널 공용.
 */
function nodeKey(e: Record<string, unknown>): string {
  const raw = String(e.tool ?? e.step ?? e.node ?? 'event');
  return raw.replace(/^tool:/, '');
}

function stepLabel(e: Record<string, unknown>): string {
  return nodeKey(e) || 'event';
}

function isGeneration(e: Record<string, unknown>): boolean {
  const k = nodeKey(e);
  return k === 'generation' || k === 'generate_with_context';
}

function hasDetail(e: Record<string, unknown>): boolean {
  if (isGeneration(e)) return e.ttft_ms != null || e.chunks_per_s != null;
  return !!e.detail;
}

export function TracePanel({ events }: { events: Array<Record<string, unknown>> }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  if (!events || events.length === 0) return null;

  const toggle = (i: number) => setExpanded((prev) => ({ ...prev, [i]: !prev[i] }));

  return (
    <div className="mt-2 border-t border-[var(--color-neutral-200)] pt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-1 py-1 text-[var(--font-size-xs)] text-[var(--color-neutral-500)] transition-colors hover:text-[var(--color-neutral-700)]"
        aria-expanded={open}
        aria-label="RAG 파이프라인 트레이스 펼치기"
      >
        <svg
          className={`h-3.5 w-3.5 shrink-0 transition-transform duration-[var(--duration-fast)] ${open ? 'rotate-90' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        <span>RAG 파이프라인 트레이스 ({events.length}단계)</span>
      </button>
      {open && (
        <ol className="mt-1 space-y-0.5 pl-1">
          {events.map((e, i) => {
            const msVal =
              typeof e.ms === 'number' ? e.ms : typeof e.latency_ms === 'number' ? e.latency_ms : null;
            const failed = e.success === false;
            const detailAvailable = hasDetail(e);
            const isRag = nodeKey(e) === 'rag_search';
            const isGen = isGeneration(e);
            const status = e.status ? String(e.status) : null;
            return (
              <li key={i} className="text-[var(--font-size-xs)]">
                <div
                  className={`flex items-center gap-2 rounded-[var(--radius-sm)] px-1 py-0.5 ${detailAvailable ? 'cursor-pointer hover:bg-[var(--color-neutral-100)]' : ''}`}
                  onClick={detailAvailable ? () => toggle(i) : undefined}
                >
                  <span className="w-4 shrink-0 text-right text-[var(--color-neutral-400)]">{i + 1}</span>
                  {detailAvailable && (
                    <svg
                      className={`h-3 w-3 shrink-0 text-[var(--color-neutral-400)] transition-transform ${expanded[i] ? 'rotate-90' : ''}`}
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  )}
                  <span className="font-mono text-[var(--color-neutral-700)]">{stepLabel(e)}</span>
                  {status && status !== 'end' && (
                    <span className="text-[var(--color-neutral-400)]">{status}</span>
                  )}
                  {failed && <span className="text-[var(--color-error)]">실패</span>}
                  {msVal != null && (
                    <span className="ml-auto shrink-0 font-mono" style={{ color: latencyColor(msVal) }}>
                      {formatDuration(msVal)}
                    </span>
                  )}
                </div>
                {detailAvailable && expanded[i] && (
                  <div className="pl-6">
                    {isRag && <RagSearchDetail detail={(e.detail ?? {}) as TraceDetail} />}
                    {isGen && <GenerationDetail e={e} />}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
