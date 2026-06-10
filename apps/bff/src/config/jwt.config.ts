import { createHash } from 'crypto';
import { readFileSync } from 'fs';

/**
 * JWT 키 설정 (D17 비대칭 전환).
 *
 * - AIP_JWT_PRIVATE_KEY_PATH 설정 시: RS256 서명. 개인키는 bff에만 존재하고
 *   apps/api는 공개키(AIP_JWT_PUBLIC_KEY_PATH)로 검증만 한다.
 * - 미설정 시: 기존 HS256(공유 시크릿) — 레거시 호환.
 * - 과도기에는 두 알고리즘 토큰을 모두 검증한다 (hs256Fallback).
 *
 * 키 파일 경로가 설정됐는데 읽기 실패면 부팅을 중단한다 —
 * 보안 설정 오류를 조용한 HS256 강등으로 흡수하지 않는다.
 */
function readPemOrThrow(path: string | undefined, label: string): string {
  if (!path) return '';
  try {
    return readFileSync(path, 'utf8');
  } catch (e) {
    throw new Error(`${label} 읽기 실패 (${path}): ${(e as Error).message}`);
  }
}

const privateKey = readPemOrThrow(process.env.AIP_JWT_PRIVATE_KEY_PATH, 'JWT 개인키');
const publicKey = readPemOrThrow(process.env.AIP_JWT_PUBLIC_KEY_PATH, 'JWT 공개키');

if (privateKey && !publicKey) {
  throw new Error(
    'AIP_JWT_PRIVATE_KEY_PATH 설정 시 AIP_JWT_PUBLIC_KEY_PATH도 필요합니다 (bff 자체 토큰 검증용)',
  );
}

export const jwtConfig = {
  secret: process.env.JWT_SECRET || 'dev-jwt-secret',
  accessExpiresIn: Number(process.env.JWT_EXPIRATION) || 900,
  refreshExpiresIn: Number(process.env.JWT_REFRESH_EXPIRATION) || 604800,
  privateKey,
  publicKey,
  algorithm: (privateKey ? 'RS256' : 'HS256') as 'RS256' | 'HS256',
  // 키 회전 대비 식별자 — 공개키 SHA-256 지문 앞 16자리. 토큰 헤더 kid로 실린다.
  kid: publicKey
    ? createHash('sha256').update(publicKey).digest('hex').slice(0, 16)
    : undefined,
  // 과도기: RS256 전환 후에도 기존 HS256 토큰 검증 허용 (만료로 자연 소멸).
  // 전환 완료 후 AIP_JWT_HS256_FALLBACK=false로 잠근다.
  hs256Fallback: (process.env.AIP_JWT_HS256_FALLBACK ?? 'true') !== 'false',
};
