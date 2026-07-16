'use client';

import { Badge } from '@/components/ui/badge';
import { FormSection } from '../form-section';
import { useProfileForm } from '../form-context';
import { TextAreaField, TextField } from '../text-field';

export function BasicSection({ errorCount }: { errorCount: number }) {
  const { isExistingProfile } = useProfileForm();

  return (
    <FormSection title="기본 정보" errorCount={errorCount}>
      <TextField
        fieldKey="id"
        label="id"
        placeholder="my-profile"
        omitWhenEmpty={false}
        disabled={isExistingProfile}
        badge={
          isExistingProfile ? (
            <Badge variant="neutral" size="sm">
              생성 후 변경 불가
            </Badge>
          ) : undefined
        }
      />
      <TextField fieldKey="name" label="name" placeholder="내 프로필" omitWhenEmpty={false} />
      <TextAreaField fieldKey="description" label="description" rows={2} />
    </FormSection>
  );
}
