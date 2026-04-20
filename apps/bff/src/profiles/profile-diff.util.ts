/**
 * Profile config diff utility — dependency-free.
 *
 * 전체 tree 재귀적으로 비교하여 added/removed/changed 반환.
 * jsondiffpatch 대체 경량 구현 (외부 의존성 최소화).
 */

export interface DiffResult {
  added: Record<string, unknown>;
  removed: Record<string, unknown>;
  changed: Record<string, { before: unknown; after: unknown }>;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function flatten(
  obj: Record<string, unknown>,
  prefix = '',
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj ?? {})) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (isObject(v)) {
      Object.assign(out, flatten(v, path));
    } else {
      out[path] = v;
    }
  }
  return out;
}

export function computeDiff(
  before: Record<string, unknown> | null | undefined,
  after: Record<string, unknown> | null | undefined,
): DiffResult {
  const beforeFlat = flatten(before ?? {});
  const afterFlat = flatten(after ?? {});

  const added: Record<string, unknown> = {};
  const removed: Record<string, unknown> = {};
  const changed: Record<string, { before: unknown; after: unknown }> = {};

  for (const k of Object.keys(afterFlat)) {
    if (!(k in beforeFlat)) {
      added[k] = afterFlat[k];
    } else if (JSON.stringify(beforeFlat[k]) !== JSON.stringify(afterFlat[k])) {
      changed[k] = { before: beforeFlat[k], after: afterFlat[k] };
    }
  }
  for (const k of Object.keys(beforeFlat)) {
    if (!(k in afterFlat)) {
      removed[k] = beforeFlat[k];
    }
  }
  return { added, removed, changed };
}
