'use client';

import { useState } from 'react';
import { formatDuration, latencyColor } from '@/lib/format';
import { ChunkViewer } from '@/components/chat/chunk-viewer';

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
  found_by?: string[];
  origin?: string;
};

type AuditRow = {
  chunk_id?: string;
  document_id?: string;
  rerank_score?: number;
  vector_score?: number;
  fused?: number;
  tier?: number;
  fate?: string;
};

type Stages = {
  expansion?: { mode?: string; queries?: number; probe_top?: number };
  retrieval?: { candidates?: number };
  neighbor?: { added?: number };
  rerank?: { input?: number; output?: number; by?: string };
  noise_filter?: { before?: number; after?: number };
};

type TraceDetail = {
  filter?: Record<string, unknown>;
  expanded_queries?: string[];
  candidates?: Chunk[];
  reranked?: Chunk[];
  reranked_by?: string;
  stages?: Stages;
  rerank_audit?: AuditRow[];
};

type GraphEdge = {
  source_name?: string;
  target_name?: string;
  relation?: string;
  reason?: string;
  strength?: string | number;
};

type GraphDiscovered = {
  file_name?: string;
  chunks?: number;
  score?: number;
  via?: string;
};

type GraphDetail = {
  seeds?: string[];
  edges?: GraphEdge[];
  discovered?: GraphDiscovered[];
  enriched?: string[];
  skipped?: Record<string, number>;
  related_total?: number;
};

function shortId(id?: string): string {
  if (!id) return '—';
  return id.length > 8 ? id.slice(0, 8) : id;
}

/** 청크 목록 (점수 내림차순 표시, 스크롤). rerank 입력/출력 공용.
 *  청크 클릭 → 청크 뷰어(전문·섹션·원본 문서 역추적). found_by/origin 귀속 뱃지 표시. */
function ChunkList({
  title,
  chunks,
  onSelect,
}: {
  title: string;
  chunks: Chunk[];
  onSelect?: (chunkId: string) => void;
}) {
  if (!chunks || chunks.length === 0) return null;
  return (
    <div className="mt-1.5">
      <div className="mb-1 text-[var(--font-size-xs)] font-semibold text-[var(--color-neutral-600)]">
        {title} ({chunks.length})
      </div>
      <ol className="max-h-56 space-y-1 overflow-y-auto pr-1">
        {chunks.map((c, i) => {
          const clickable = !!(onSelect && c.chunk_id);
          return (
            <li
              key={c.chunk_id ?? i}
              className={`rounded-[var(--radius-sm)] bg-[var(--color-neutral-50)] px-2 py-1 text-[var(--font-size-xs)] ${clickable ? 'cursor-pointer transition-colors hover:bg-[var(--color-neutral-100)]' : ''}`}
              onClick={clickable ? () => onSelect!(c.chunk_id!) : undefined}
              role={clickable ? 'button' : undefined}
              aria-label={clickable ? `청크 ${shortId(c.chunk_id)} 전문 보기` : undefined}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-4 shrink-0 text-right text-[var(--color-neutral-400)]">{i + 1}</span>
                <span className="font-mono text-[var(--color-neutral-500)]">doc {shortId(c.document_id)}</span>
                <span className="font-mono text-[var(--color-neutral-400)]">#{shortId(c.chunk_id)}</span>
                {c.origin === 'neighbor' && (
                  <span className="rounded-[var(--radius-sm)] bg-[var(--color-info-light)] px-1 text-[var(--color-info)]">
                    이웃확장
                  </span>
                )}
                {c.found_by && c.found_by.length > 0 && (
                  <span className="rounded-[var(--radius-sm)] bg-[var(--color-neutral-100)] px-1 text-[var(--color-neutral-500)]">
                    {c.found_by.join('·')}
                  </span>
                )}
                {typeof c.score === 'number' && (
                  <span className="ml-auto shrink-0 font-mono text-[var(--color-primary-600)]">
                    {c.score.toFixed(4)}
                  </span>
                )}
              </div>
              {c.snippet && (
                <p className="mt-0.5 pl-6 text-[var(--color-neutral-500)] line-clamp-2">{c.snippet}</p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

const FATE_LABEL: Record<string, { text: string; cls: string }> = {
  selected: { text: '채택', cls: 'bg-[var(--color-success-light)] text-[var(--color-success)]' },
  capacity: { text: '정원 밖', cls: 'bg-[var(--color-neutral-100)] text-[var(--color-neutral-500)]' },
  tier_fail: { text: '티어 미달', cls: 'bg-[var(--color-neutral-100)] text-[var(--color-neutral-400)]' },
  noise_cut: { text: '노이즈컷', cls: 'bg-[var(--color-warning-light)] text-[var(--color-warning)]' },
};

/** 리랭킹 전수 판정 감사 — 전 후보의 점수·티어·채택/탈락 사유 (역방향 분석의 핵심) */
function RerankAudit({
  audit,
  onSelect,
}: {
  audit: AuditRow[];
  onSelect?: (chunkId: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  if (!audit || audit.length === 0) return null;
  const rows = showAll ? audit : audit.slice(0, 12);
  return (
    <div className="mt-1.5">
      <div className="mb-1 text-[var(--font-size-xs)] font-semibold text-[var(--color-neutral-600)]">
        리랭킹 판정 감사 ({audit.length}후보 — 채택/탈락 사유)
      </div>
      <ol className="max-h-56 space-y-0.5 overflow-y-auto pr-1">
        {rows.map((a, i) => {
          const fate = FATE_LABEL[a.fate ?? ''] ?? {
            text: a.fate ?? '—',
            cls: 'bg-[var(--color-neutral-100)] text-[var(--color-neutral-500)]',
          };
          const clickable = !!(onSelect && a.chunk_id);
          return (
            <li
              key={a.chunk_id ?? i}
              className={`flex items-center gap-2 rounded-[var(--radius-sm)] px-2 py-0.5 text-[var(--font-size-xs)] ${clickable ? 'cursor-pointer transition-colors hover:bg-[var(--color-neutral-100)]' : ''}`}
              onClick={clickable ? () => onSelect!(a.chunk_id!) : undefined}
            >
              <span className="w-4 shrink-0 text-right text-[var(--color-neutral-400)]">{i + 1}</span>
              <span className="font-mono text-[var(--color-neutral-400)]">#{shortId(a.chunk_id)}</span>
              <span className="font-mono text-[var(--color-neutral-500)]">
                rr {a.rerank_score?.toFixed(2) ?? '—'} · vec {a.vector_score?.toFixed(2) ?? '—'}
              </span>
              <span className="font-mono font-semibold text-[var(--color-neutral-700)]">
                = {a.fused?.toFixed(3) ?? '—'}
              </span>
              {typeof a.tier === 'number' && a.tier > 0 && (
                <span className="text-[var(--color-neutral-400)]">T{a.tier}</span>
              )}
              <span className={`ml-auto shrink-0 rounded-[var(--radius-sm)] px-1 ${fate.cls}`}>
                {fate.text}
              </span>
            </li>
          );
        })}
      </ol>
      {audit.length > 12 && (
        <button
          onClick={() => setShowAll((v) => !v)}
          className="mt-0.5 text-[var(--font-size-xs)] text-[var(--color-primary-600)] hover:underline focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-600)]"
          aria-label={showAll ? '감사 목록 접기' : '감사 목록 전체 보기'}
        >
          {showAll ? '접기' : `전체 ${audit.length}건 보기`}
        </button>
      )}
    </div>
  );
}

/** 5-Layer 파이프라인 단계 요약: 각 레이어에서 무엇이 얼마나 걸러졌나 */
function StagesSummary({ stages }: { stages: Stages }) {
  const parts: string[] = [];
  const exp = stages.expansion;
  if (exp) {
    const mode =
      exp.mode === 'probe_skip' ? '확장 스킵' : exp.mode === 'expanded' ? '확장' : '확장 없음';
    const probe = typeof exp.probe_top === 'number' ? ` (probe ${exp.probe_top.toFixed(4)})` : '';
    parts.push(`${mode} · ${exp.queries ?? 1}쿼리${probe}`);
  }
  if (stages.retrieval) parts.push(`후보 ${stages.retrieval.candidates ?? 0}`);
  if (stages.neighbor && (stages.neighbor.added ?? 0) > 0) parts.push(`이웃 +${stages.neighbor.added}`);
  const rr = stages.rerank;
  if (rr) parts.push(`리랭킹 ${rr.input ?? '?'}→${rr.output ?? '?'}${rr.by ? ` (${rr.by})` : ''}`);
  const nf = stages.noise_filter;
  if (nf && nf.before !== nf.after) parts.push(`노이즈컷 ${nf.before}→${nf.after}`);
  if (parts.length === 0) return null;
  return (
    <div className="text-[var(--font-size-xs)] text-[var(--color-neutral-600)]">
      <span className="font-semibold">파이프라인:</span> {parts.join(' → ')}
    </div>
  );
}

/** graph_enrich 상세: 온톨로지 탐색 결과 — 시드, 관계 엣지, 발견/보강 문서, 필터 사유 */
function GraphEnrichDetail({ detail }: { detail: GraphDetail }) {
  const edges = detail.edges ?? [];
  const discovered = detail.discovered ?? [];
  const enriched = detail.enriched ?? [];
  const skipped = Object.entries(detail.skipped ?? {}).filter(([, v]) => v > 0);
  const skippedLabel: Record<string, string> = {
    unmapped: '미동기화',
    security: '보안등급',
    no_ontology: '온톨로지 없음',
  };
  return (
    <div className="mt-1 space-y-1.5 border-l-2 border-[var(--color-neutral-200)] pl-3 text-[var(--font-size-xs)]">
      {detail.seeds && detail.seeds.length > 0 && (
        <div className="text-[var(--color-neutral-600)]">
          <span className="font-semibold">탐색 시드:</span> {detail.seeds.join(', ')}
        </div>
      )}
      {edges.length > 0 && (
        <div>
          <div className="mb-1 font-semibold text-[var(--color-neutral-600)]">
            지식그래프 관계 ({edges.length})
          </div>
          <ol className="space-y-1">
            {edges.map((edge, i) => (
              <li
                key={i}
                className="rounded-[var(--radius-sm)] bg-[var(--color-neutral-50)] px-2 py-1"
              >
                <div className="flex flex-wrap items-center gap-1 text-[var(--color-neutral-700)]">
                  <span className="truncate">{edge.source_name || '—'}</span>
                  <span className="shrink-0 font-mono text-[var(--color-primary-600)]">
                    ─{edge.relation || '관련'}→
                  </span>
                  <span className="truncate">{edge.target_name || '—'}</span>
                  {edge.strength != null && String(edge.strength).trim() !== '' && (
                    <span className="ml-auto shrink-0 font-mono text-[var(--color-neutral-500)]">
                      강도 {String(edge.strength)}/10
                    </span>
                  )}
                </div>
                {edge.reason && (
                  <p className="mt-0.5 text-[var(--color-neutral-500)]">사유: {edge.reason}</p>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
      {discovered.length > 0 && (
        <div className="text-[var(--color-neutral-600)]">
          <span className="font-semibold">발견 문서 (새로 합류):</span>
          <ul className="mt-0.5 space-y-0.5 pl-3">
            {discovered.map((doc, i) => (
              <li key={i} className="text-[var(--color-neutral-500)]">
                · {doc.file_name}
                {doc.via ? ` — ${doc.via}` : ''}
                {typeof doc.chunks === 'number' && doc.chunks > 0 ? ` · ${doc.chunks}청크` : ' · 헤더만'}
                {typeof doc.score === 'number' && (
                  <span className="font-mono text-[var(--color-primary-600)]"> {doc.score.toFixed(4)}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {enriched.length > 0 && (
        <div className="text-[var(--color-neutral-600)]">
          <span className="font-semibold">보강 (관계 메타 추가):</span>{' '}
          <span className="text-[var(--color-neutral-500)]">{enriched.join(', ')}</span>
        </div>
      )}
      {skipped.length > 0 && (
        <div className="text-[var(--color-neutral-400)]">
          걸러짐: {skipped.map(([k, v]) => `${skippedLabel[k] ?? k} ${v}건`).join(' · ')}
        </div>
      )}
      {edges.length === 0 && discovered.length === 0 && enriched.length === 0 && (
        <div className="text-[var(--color-neutral-400)]">그래프 관계 없음 (탐색 결과 0건)</div>
      )}
    </div>
  );
}

/** rag_search 상세: 필터 기준 + 확장쿼리 + 리랭킹 입력/최종 청크 */
function RagSearchDetail({
  detail,
  onSelectChunk,
}: {
  detail: TraceDetail;
  onSelectChunk?: (chunkId: string) => void;
}) {
  const filter = detail.filter ?? {};
  const domains = filter.domain_codes as string[] | null | undefined;
  return (
    <div className="mt-1 border-l-2 border-[var(--color-neutral-200)] pl-3">
      {/* 5-Layer 단계 요약 */}
      {detail.stages && <StagesSummary stages={detail.stages} />}
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
        onSelect={onSelectChunk}
      />
      {/* 리랭킹 전수 판정 (왜 채택/탈락했나 — 역방향 분석) */}
      <RerankAudit audit={detail.rerank_audit ?? []} onSelect={onSelectChunk} />
      {/* 최종 채택 청크 (어떤 청크를 잡았나) */}
      <ChunkList title="최종 채택 청크" chunks={detail.reranked ?? []} onSelect={onSelectChunk} />
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
  const [viewerChunkId, setViewerChunkId] = useState<string | null>(null);
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
                    {isRag && (
                      <RagSearchDetail
                        detail={(e.detail ?? {}) as TraceDetail}
                        onSelectChunk={setViewerChunkId}
                      />
                    )}
                    {nodeKey(e) === 'graph_enrich' && (
                      <GraphEnrichDetail detail={(e.detail ?? {}) as GraphDetail} />
                    )}
                    {isGen && <GenerationDetail e={e} />}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {/* 청크 역추적 뷰어 (전문·섹션·원본 문서 링크) */}
      {viewerChunkId && (
        <ChunkViewer chunkId={viewerChunkId} onClose={() => setViewerChunkId(null)} />
      )}
    </div>
  );
}
