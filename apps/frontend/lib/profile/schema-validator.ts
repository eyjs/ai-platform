/**
 * ajv 기반 Profile 검증.
 *
 * 검증 규칙의 유일한 출처는 BFF 가 내려주는 JSON Schema 다. 이 파일에는
 * enum·범위·필수 필드 목록이 없다 — 전부 스키마에서 온다. 여기서 하는 일은
 * (1) 스키마 컴파일, (2) ajv 에러를 "어느 필드의 문제인가"로 환원, (3) 한국어 문구.
 */

import Ajv, { type ErrorObject, type ValidateFunction } from 'ajv';
import type { FieldIssue, ProfileField } from '@/types/profile';
import type { JsonSchema } from './schema-meta';

/**
 * strict:false — 스키마에 ajv 가 모르는 주석성 키워드가 있어도 죽지 않도록.
 * allErrors:true — 필드별로 모아 보여줘야 하므로 첫 에러에서 멈추면 안 된다.
 */
export function compileProfileValidator(schema: JsonSchema): ValidateFunction {
  const ajv = new Ajv({
    allErrors: true,
    strict: false,
    allowUnionTypes: true,
  });
  return ajv.compile(schema);
}

/** instancePath("/tools/0/name") → 최상위 필드("tools") */
function fieldFromPath(path: string): ProfileField | null {
  if (!path) return null;
  const first = path.replace(/^\//, '').split('/')[0];
  const decoded = first.replace(/~1/g, '/').replace(/~0/g, '~');
  return decoded ? (decoded as ProfileField) : null;
}

/** ajv 에러가 가리키는 필드. required 는 instancePath 가 비어 있어 params 에서 꺼낸다. */
function resolveField(error: ErrorObject): ProfileField | null {
  const fromPath = fieldFromPath(error.instancePath);
  if (fromPath) return fromPath;

  const params: Record<string, unknown> = error.params;
  const missing = params.missingProperty;
  if (typeof missing === 'string') return missing as ProfileField;
  const additional = params.additionalProperty;
  if (typeof additional === 'string') return additional as ProfileField;
  return null;
}

function joinValues(values: unknown): string {
  return Array.isArray(values) ? values.map(String).join(', ') : '';
}

/** ajv 영문 메시지를 화면에 그대로 노출하지 않기 위한 한국어 변환. */
function toKoreanMessage(error: ErrorObject): string {
  const params: Record<string, unknown> = error.params;

  switch (error.keyword) {
    case 'required':
      return '필수 항목입니다';
    case 'enum':
      return `허용된 값: ${joinValues(params.allowedValues)}`;
    case 'type':
      return `타입이 올바르지 않습니다 (${String(params.type)})`;
    case 'pattern':
      return `형식이 올바르지 않습니다 (${String(params.pattern)})`;
    case 'minimum':
      return `${String(params.limit)} 이상이어야 합니다`;
    case 'maximum':
      return `${String(params.limit)} 이하여야 합니다`;
    case 'minLength':
      return `${String(params.limit)}자 이상 입력하세요`;
    case 'maxLength':
      return `${String(params.limit)}자 이하로 입력하세요`;
    case 'minItems':
      return `${String(params.limit)}개 이상이어야 합니다`;
    case 'uniqueItems':
      return '중복된 값이 있습니다';
    case 'additionalProperties':
      return `스키마에 없는 항목입니다: ${String(params.additionalProperty)}`;
    default:
      return error.message ?? '유효하지 않은 값입니다';
  }
}

/**
 * if/then 분기 자체(keyword 'if')는 사용자에게 의미가 없다.
 * 실제로 위반된 조건(then 안의 required 등)이 별도 에러로 이미 나오므로 버린다.
 */
function isNoiseError(error: ErrorObject): boolean {
  return error.keyword === 'if' || error.keyword === 'anyOf' || error.keyword === 'oneOf';
}

export function mapAjvErrors(errors: ErrorObject[] | null | undefined): FieldIssue[] {
  if (!errors) return [];

  const issues: FieldIssue[] = [];
  const seen = new Set<string>();

  for (const error of errors) {
    if (isNoiseError(error)) continue;

    const field = resolveField(error);
    const message = toKoreanMessage(error);
    const path =
      error.keyword === 'required' && field
        ? `${error.instancePath}/${field}`
        : error.instancePath;

    const dedupeKey = `${field ?? ''}|${path}|${message}`;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);

    issues.push({ field, path, message, severity: 'error' });
  }

  return issues;
}

/** 스키마로 config 를 검증하고 필드 단위 이슈 목록을 돌려준다. */
export function validateProfile(validate: ValidateFunction, config: unknown): FieldIssue[] {
  validate(config);
  return mapAjvErrors(validate.errors);
}
