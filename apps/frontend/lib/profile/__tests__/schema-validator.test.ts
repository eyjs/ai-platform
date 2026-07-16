/**
 * ajv 에러 → 필드 매핑 테스트.
 *
 * 여기서 쓰는 스키마는 실제 스키마의 '사본'이 아니라 매핑 동작을 확인하기 위한 최소
 * 구조다 (실제 enum 을 여기에 적으면 그것 자체가 또 하나의 진실원천 사본이 된다).
 * 구조만 실제와 같게 둔다: required / enum / pattern / if-then / contains.
 */

import { describe, expect, it } from 'vitest';
import { compileProfileValidator, validateProfile } from '../schema-validator';
import type { JsonSchema } from '../schema-meta';

const SCHEMA: JsonSchema = {
  type: 'object',
  required: ['id', 'name', 'mode'],
  additionalProperties: false,
  properties: {
    id: { type: 'string', pattern: '^[a-z0-9][a-z0-9_-]{1,63}$' },
    name: { type: 'string', minLength: 1 },
    mode: { type: 'string', enum: ['deterministic', 'agentic', 'workflow', 'hybrid'] },
    workflow_id: { type: ['string', 'null'] },
    max_tool_calls: { type: 'integer', minimum: 1, maximum: 20 },
    memory_scopes: {
      type: 'array',
      items: { type: 'string', enum: ['local', 'user', 'project'] },
    },
    memory_project_id: { type: ['string', 'null'] },
    tools: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name'],
        properties: { name: { type: 'string', minLength: 1 } },
      },
    },
  },
  allOf: [
    {
      if: { required: ['mode'], properties: { mode: { const: 'workflow' } } },
      then: { required: ['workflow_id'], properties: { workflow_id: { type: 'string', minLength: 1 } } },
    },
    {
      if: {
        required: ['memory_scopes'],
        properties: { memory_scopes: { type: 'array', contains: { const: 'project' } } },
      },
      then: {
        required: ['memory_project_id'],
        properties: { memory_project_id: { type: 'string', minLength: 1 } },
      },
    },
  ],
};

const validator = compileProfileValidator(SCHEMA);
const VALID = { id: 'fortune-saju', name: '사주', mode: 'agentic' };

describe('스키마 검증', () => {
  it('유효한 설정은 이슈가 없다', () => {
    expect(validateProfile(validator, VALID)).toEqual([]);
  });

  it('빠진 필수 필드를 해당 필드에 매단다', () => {
    const issues = validateProfile(validator, { mode: 'agentic' });
    const fields = issues.map((issue) => issue.field);
    expect(fields).toContain('id');
    expect(fields).toContain('name');
    expect(issues[0].severity).toBe('error');
  });

  it('enum 위반 메시지에 허용값이 담긴다', () => {
    const issues = validateProfile(validator, { ...VALID, mode: 'creative' });
    expect(issues).toHaveLength(1);
    expect(issues[0].field).toBe('mode');
    expect(issues[0].message).toContain('deterministic');
  });

  it('id 패턴 위반을 id 필드에 매단다', () => {
    const issues = validateProfile(validator, { ...VALID, id: 'Bad Id!' });
    expect(issues.map((i) => i.field)).toEqual(['id']);
  });

  it('범위 위반을 한국어로 알린다', () => {
    const issues = validateProfile(validator, { ...VALID, max_tool_calls: 99 });
    expect(issues[0].field).toBe('max_tool_calls');
    expect(issues[0].message).toBe('20 이하여야 합니다');
  });

  it('mode=workflow 면 workflow_id 를 요구한다', () => {
    const issues = validateProfile(validator, { ...VALID, mode: 'workflow' });
    expect(issues.map((i) => i.field)).toContain('workflow_id');
    // if/then 분기 자체는 사용자에게 노출하지 않는다.
    expect(issues.every((i) => !i.message.includes('if'))).toBe(true);
  });

  it('memory_scopes 에 project 가 있으면 memory_project_id 를 요구한다', () => {
    const issues = validateProfile(validator, { ...VALID, memory_scopes: ['local', 'project'] });
    expect(issues.map((i) => i.field)).toContain('memory_project_id');
  });

  it('memory_scopes 의 user 는 정상 값이다', () => {
    expect(validateProfile(validator, { ...VALID, memory_scopes: ['local', 'user'] })).toEqual([]);
  });

  it('중첩 배열 오류를 최상위 필드로 환원한다', () => {
    const issues = validateProfile(validator, { ...VALID, tools: [{ name: '' }] });
    expect(issues[0].field).toBe('tools');
    expect(issues[0].path).toBe('/tools/0/name');
  });

  it('스키마에 없는 키를 오류로 알린다 (additionalProperties:false)', () => {
    const issues = validateProfile(validator, { ...VALID, bogus: 1 });
    expect(issues[0].field).toBe('bogus');
    expect(issues[0].message).toContain('스키마에 없는 항목');
  });
});
