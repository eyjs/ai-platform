import { getAccessToken } from '@/lib/auth/token-storage';

/**
 * 역방향 분석 API — 트레이스의 chunk_id/document_id 로 근거를 역추적한다.
 * apps/api 직결 (청크/문서 데이터는 FastAPI 소관, ADMIN 전용).
 */

const FASTAPI_URL = process.env.NEXT_PUBLIC_FASTAPI_URL || 'http://localhost:8000';

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
    throw new Error(body.detail || `요청 실패 (${res.status})`);
  }
  return res.json();
}

export interface ChunkDetail {
  chunk_id: string;
  document_id: string;
  content: string;
  chunk_index: number;
  token_count: number;
  metadata: { section_path?: string[]; section_level?: number; section_part?: number };
  domain_code: string;
  security_level: string;
  document: {
    title: string;
    file_name: string;
    external_id: string | null;
    source_url: string | null;
    total_chunks: number;
  };
}

export interface DocumentMeta {
  document_id: string;
  title: string;
  file_name: string;
  domain_code: string;
  security_level: string;
  external_id: string | null;
  source_url: string | null;
  created_at: string | null;
  total_chunks: number;
}

export interface DocumentChunk {
  chunk_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
  metadata: { section_path?: string[]; section_level?: number; section_part?: number };
}

export async function getChunkDetail(chunkId: string): Promise<ChunkDetail> {
  const res = await fetch(`${FASTAPI_URL}/api/admin/chunks/${chunkId}`, {
    headers: authHeaders(),
  });
  return handleResponse<ChunkDetail>(res);
}

export async function getDocumentMeta(documentId: string): Promise<DocumentMeta> {
  const res = await fetch(`${FASTAPI_URL}/api/admin/documents/${documentId}`, {
    headers: authHeaders(),
  });
  return handleResponse<DocumentMeta>(res);
}

export async function getDocumentChunks(
  documentId: string,
  offset = 0,
  limit = 200,
): Promise<{ document_id: string; offset: number; chunks: DocumentChunk[] }> {
  const res = await fetch(
    `${FASTAPI_URL}/api/admin/documents/${documentId}/chunks?offset=${offset}&limit=${limit}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}
