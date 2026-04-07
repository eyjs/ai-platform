import { NextRequest, NextResponse } from 'next/server';

const PUBLIC_PATHS = ['/login', '/api'];
const ADMIN_PATHS = ['/admin'];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // 공개 경로는 통과
  if (PUBLIC_PATHS.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // 정적 파일 통과
  if (
    pathname.startsWith('/_next') ||
    pathname.startsWith('/favicon') ||
    pathname.includes('.')
  ) {
    return NextResponse.next();
  }

  // 토큰 확인 (쿠키 또는 Authorization 헤더)
  // 클라이언트에서 메모리에 저장하므로, 미들웨어에서는 리프레시 토큰 쿠키만 확인
  // 실제 인증 검증은 클라이언트 AuthProvider에서 수행
  const refreshToken = request.cookies.get('aip-refresh-token');
  const hasToken =
    refreshToken ||
    request.headers.get('authorization')?.startsWith('Bearer ');

  // 완전한 서버사이드 인증은 AuthProvider가 담당
  // 미들웨어는 UX 최적화 목적으로만 사용
  // 토큰이 전혀 없으면 login으로 리다이렉트
  // (localStorage 기반이므�� 미들웨어에서 완벽하게 검증할 수 없음)

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
