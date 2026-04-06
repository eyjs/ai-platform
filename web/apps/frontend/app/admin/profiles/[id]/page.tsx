'use client';

import { use } from 'react';
import { EditorLayout } from '@/components/admin/profile-editor/editor-layout';

export default function ProfileEditPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return <EditorLayout profileId={id} />;
}
