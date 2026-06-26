import { redirect } from 'next/navigation';

/** 루트 — 어드민 셸(모니터링 대시보드)로 보낸다. */
export default function RootPage() {
  redirect('/admin/dashboard');
}
