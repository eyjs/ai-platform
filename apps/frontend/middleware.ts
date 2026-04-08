import { NextRequest, NextResponse } from 'next/server';

const PUBLIC_PATHS = ['/login', '/api'];
const AUTH_MARKER_COOKIE = 'aip_authenticated';

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // 공개 경로 통과
  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return NextResponse.next();
  }

  // 정적/내부 파일 통과
  if (
    pathname.startsWith('/_next') ||
    pathname.startsWith('/favicon') ||
    pathname.includes('.')
  ) {
    return NextResponse.next();
  }

  // 마커 쿠키 확인 — 실제 JWT 는 localStorage 에, 미들웨어는 존재만 확인
  const marker = request.cookies.get(AUTH_MARKER_COOKIE);
  if (!marker || marker.value !== '1') {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = '/login';
    loginUrl.search = `?callbackUrl=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
