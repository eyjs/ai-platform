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
  pendingDocuments: number;
  completedDocuments: number;
  failedDocuments: number;
  totalChunks: number;
  avgChunksPerDocument: number;
  documentsByStatus: Array<{ status: string; count: number }>;
  documentsBySource: Array<{ source: string; count: number }>;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  source: string | null;
  status: string;
  fileSize: number | null;
  mimeType: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeDocumentsResponse {
  items: KnowledgeDocument[];
  total: number;
  page: number;
  size: number;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  content: string | null;
  filePath: string | null;
  chunkCount: number;
}

export interface ReindexResponse {
  jobId: string;
  documentId: string;
  status: string;
  message: string;
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
  source?: string;
  status?: string;
}): Promise<KnowledgeDocumentsResponse> {
  const query = new URLSearchParams();
  if (params.page) query.set('page', String(params.page));
  if (params.size) query.set('size', String(params.size));
  if (params.source) query.set('source', params.source);
  if (params.status) query.set('status', params.status);

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

export async function reindexDocument(id: string): Promise<ReindexResponse> {
  const res = await fetch(`${BFF_URL}/knowledge/reindex/${id}`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse(res);
}
