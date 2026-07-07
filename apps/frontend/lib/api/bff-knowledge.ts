import { getAccessToken } from '@/lib/auth/token-storage';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001/bff';

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
  avgChunksPerDocument: number;
  documentsByDomain: Array<{ domain: string; count: number }>;
  documentsBySecurityLevel: Array<{ level: string; count: number }>;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  fileName: string | null;
  domainCode: string | null;
  securityLevel: string | null;
  sourceUrl: string | null;
  createdAt: string;
}

export interface KnowledgeDocumentsResponse {
  items: KnowledgeDocument[];
  total: number;
  page: number;
  size: number;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  content: string | null;
  chunkCount: number;
  metadata: Record<string, unknown> | null;
}


export async function fetchKnowledgeStats(): Promise<KnowledgeStats> {
  const res = await fetch(`${BFF_URL}/knowledge/stats`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchKnowledgeDocuments(params: {
  page?: number;
  size?: number;
  domainCode?: string;
  securityLevel?: string;
}): Promise<KnowledgeDocumentsResponse> {
  const query = new URLSearchParams();
  if (params.page) query.set('page', String(params.page));
  if (params.size) query.set('size', String(params.size));
  if (params.domainCode) query.set('domainCode', params.domainCode);
  if (params.securityLevel) query.set('securityLevel', params.securityLevel);

  const res = await fetch(`${BFF_URL}/knowledge/documents?${query}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function fetchKnowledgeDocumentDetail(id: string): Promise<KnowledgeDocumentDetail> {
  const res = await fetch(`${BFF_URL}/knowledge/documents/${id}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

