import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
    throw new Error(error.message || `요청 실패 (${res.status})`);
  }
  return res.json();
}

export interface KnowledgeStats {
  totalDocuments: number;
  totalChunks: number;
  domainDistribution: Array<{ domainCode: string; domainName: string; count: number }>;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  domainCode: string;
  domainName: string;
  chunkCount: number;
  status: 'indexed' | 'processing' | 'error';
  indexedAt: string;
}

export interface KnowledgeDocumentsResponse {
  data: KnowledgeDocument[];
  total: number;
  page: number;
  size: number;
}

export interface KnowledgeDocumentDetail {
  id: string;
  title: string;
  domainCode: string;
  domainName: string;
  status: 'indexed' | 'processing' | 'error';
  indexedAt: string;
  contentPreview: string;
  chunks: Array<{
    order: number;
    length: number;
    embeddingStatus: 'completed' | 'pending' | 'error';
  }>;
}

export async function fetchKnowledgeStats(): Promise<KnowledgeStats> {
  const res = await fetch(`${BFF_URL}/bff/knowledge/stats`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchKnowledgeDocuments(params: {
  page?: number;
  size?: number;
  domainCode?: string;
  status?: string;
}): Promise<KnowledgeDocumentsResponse> {
  const query = new URLSearchParams();
  if (params.page) query.set('page', String(params.page));
  if (params.size) query.set('size', String(params.size));
  if (params.domainCode) query.set('domain_code', params.domainCode);
  if (params.status) query.set('status', params.status);

  const res = await fetch(`${BFF_URL}/bff/knowledge/documents?${query}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchKnowledgeDocumentDetail(id: string): Promise<KnowledgeDocumentDetail> {
  const res = await fetch(`${BFF_URL}/bff/knowledge/documents/${id}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function reindexDocument(id: string): Promise<{ success: boolean }> {
  const res = await fetch(`${BFF_URL}/bff/knowledge/reindex/${id}`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse(res);
}
