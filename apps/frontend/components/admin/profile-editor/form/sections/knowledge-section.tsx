'use client';

import { Badge } from '@/components/ui/badge';
import { BooleanField } from '../boolean-field';
import { EnumField } from '../enum-field';
import { FormSection } from '../form-section';
import { NumberField } from '../number-field';
import { StringListField } from '../string-list-field';

export function KnowledgeSection({ errorCount }: { errorCount: number }) {
  return (
    <FormSection title="지식/검색" errorCount={errorCount}>
      <StringListField
        fieldKey="domain_scopes"
        label="domain_scopes"
        placeholder="도메인 코드 입력 후 Enter"
      />

      {/*
        category_scopes 는 런타임에 질의로 이어지지 않는다. 편집 가능한 것처럼 두면
        "설정했는데 왜 안 되지"가 반복되므로 읽기 전용으로 사실을 드러낸다.
      */}
      <StringListField
        fieldKey="category_scopes"
        label="category_scopes"
        disabled
        badge={
          <Badge variant="warning" size="sm">
            동작 안 함
          </Badge>
        }
      />

      <BooleanField fieldKey="include_common" label="include_common (공통 지식 포함)" />
      <EnumField fieldKey="security_level_max" label="security_level_max" />
      <NumberField
        fieldKey="rag_min_rerank_score"
        label="rag_min_rerank_score"
        isInteger={false}
        step={0.01}
      />
    </FormSection>
  );
}
