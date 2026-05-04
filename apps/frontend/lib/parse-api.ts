'use client';

import { getAccessToken } from '@/lib/auth/token-storage';
import type {
  ParseSuccessResponse,
  ParseErrorResponse,
  ParseHealthResponse,
} from '@/types/parse';

const BFF_URL = (process.env.NEXT_PUBLIC_BFF_URL || 'http://localhost:4000/bff').replace(/\/+$/, '');

function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * PDF 파일을 BFF에 업로드하여 동기 파싱을 수행한다.
 * BFF 내부에서 DocForge에 전달 -> 마크다운 변환 결과 반환.
 *
 * @param file - 브라우저 File 객체 (application/pdf)
 * @returns 파싱 성공 응답 (markdown, metadata, stats)
 * @throws Error - 네트워크 에러 또는 BFF 에러 응답
 */
export async function uploadPdf(
  file: File,
): Promise<ParseSuccessResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch(`${BFF_URL}/parse/upload`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });

  const body = (await res.json()) as ParseSuccessResponse | ParseErrorResponse;

  if (!res.ok || !body.success) {
    const err = body as ParseErrorResponse;
    throw new Error(
      err.error?.message || `PDF 파싱 요청 실패 (HTTP ${res.status})`,
    );
  }

  return body as ParseSuccessResponse;
}

/**
 * DocForge 서버 상태를 확인한다.
 */
export async function checkParseHealth(): Promise<ParseHealthResponse> {
  const res = await fetch(`${BFF_URL}/parse/health`, {
    headers: authHeaders(),
  });

  const body = (await res.json()) as ParseHealthResponse | ParseErrorResponse;

  if (!res.ok || !body.success) {
    const err = body as ParseErrorResponse;
    throw new Error(
      err.error?.message || `상태 확인 실패 (HTTP ${res.status})`,
    );
  }

  return body as ParseHealthResponse;
}
