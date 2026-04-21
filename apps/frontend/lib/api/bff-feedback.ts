import { getAccessToken } from '@/lib/auth/token-storage';
import type {
  AdminFeedbackListQuery,
  AdminFeedbackPage,
  SubmitFeedbackBody,
  SubmitFeedbackResponse,
} from '@/types/feedback';

const BFF_URL = process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:3001';

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response, label: string): Promise<T> {
  if (!res.ok) {
    let detail = '';
    try {
      detail = await res.text();
    } catch {
      // ignore
    }
    throw new Error(`${label} 실패 (${res.status}) ${detail.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

/** POST /bff/feedback — 👍/👎 피드백 전송 */
export async function submitFeedback(
  body: SubmitFeedbackBody,
): Promise<SubmitFeedbackResponse> {
  const res = await fetch(`${BFF_URL}/bff/feedback`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  return handleResponse<SubmitFeedbackResponse>(res, 'feedback submit');
}

/** GET /bff/admin/feedback — admin 리스트 조회 */
export async function fetchAdminFeedback(
  query: AdminFeedbackListQuery = {},
): Promise<AdminFeedbackPage> {
  const params = new URLSearchParams();
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  if (query.offset !== undefined) params.set('offset', String(query.offset));
  if (query.only_negative) params.set('only_negative', 'true');
  if (query.date_from) params.set('date_from', query.date_from);
  if (query.date_to) params.set('date_to', query.date_to);

  const qs = params.toString();
  const url = `${BFF_URL}/bff/admin/feedback${qs ? `?${qs}` : ''}`;
  const res = await fetch(url, { headers: authHeaders() });
  return handleResponse<AdminFeedbackPage>(res, 'admin feedback list');
}
