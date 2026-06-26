import { redirect } from 'next/navigation';

/** 어드민 랜딩 — 모니터링 대시보드로 보낸다. */
export default function AdminIndexPage() {
  redirect('/admin/dashboard');
}
