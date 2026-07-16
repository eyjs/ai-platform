/**
 * Profile JSON Schema 읽기 헬퍼.
 *
 * 스키마는 런타임에 BFF(GET /profiles/schema)에서 받아온 `unknown` JSON 이다.
 * 이 모듈은 그 JSON 에서 필드별 메타(설명/enum/min/max/default)를 안전하게 꺼내는
 * 유일한 통로다. **enum·기본값·범위를 프론트엔드에 다시 적지 않는다.**
 */

import type { ProfileConfig, ProfileField } from '@/types/profile';

export type JsonSchema = Record<string, unknown>;

export interface FieldMeta {
  key: ProfileField;
  /** 스키마 description. 없으면 빈 문자열. */
  description: string;
  /** 스칼라 enum 목록. 스키마에 enum 이 없으면 null. */
  enumValues: string[] | null;
  /** 배열 items 의 enum 목록. 없으면 null. */
  itemEnumValues: string[] | null;
  minimum: number | null;
  maximum: number | null;
  minLength: number | null;
  maxLength: number | null;
  pattern: string | null;
  defaultValue: unknown;
  isRequired: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function asStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const strings = value.filter((item): item is string => typeof item === 'string');
  return strings.length === value.length ? strings : null;
}

/** 스키마의 properties 맵. */
export function getProperties(schema: JsonSchema): Record<string, unknown> {
  const props = schema.properties;
  return isRecord(props) ? props : {};
}

/** 특정 필드의 raw 스키마 조각. */
export function getFieldSchema(schema: JsonSchema, key: ProfileField): Record<string, unknown> | null {
  const prop = getProperties(schema)[key];
  return isRecord(prop) ? prop : null;
}

/** 스키마에 해당 필드가 정의되어 있는지. */
export function hasField(schema: JsonSchema, key: ProfileField): boolean {
  return getFieldSchema(schema, key) !== null;
}

function getRequiredKeys(schema: JsonSchema): string[] {
  return asStringArray(schema.required) ?? [];
}

/**
 * 필드 메타 추출. 스키마에 없는 필드면 null 을 돌려준다 —
 * 호출자는 이 경우 "스키마에 없는 필드"임을 UI 에 드러내야 한다.
 */
export function getFieldMeta(schema: JsonSchema, key: ProfileField): FieldMeta | null {
  const field = getFieldSchema(schema, key);
  if (!field) return null;

  const items = isRecord(field.items) ? field.items : null;

  return {
    key,
    description: asString(field.description) ?? '',
    enumValues: asStringArray(field.enum),
    itemEnumValues: items ? asStringArray(items.enum) : null,
    minimum: asNumber(field.minimum),
    maximum: asNumber(field.maximum),
    minLength: asNumber(field.minLength),
    maxLength: asNumber(field.maxLength),
    pattern: asString(field.pattern),
    defaultValue: field.default,
    isRequired: getRequiredKeys(schema).includes(key),
  };
}

/** "1–20", "60–86400" 같은 범위 문자열. 둘 다 없으면 null. */
export function formatRange(meta: FieldMeta): string | null {
  const { minimum, maximum } = meta;
  if (minimum !== null && maximum !== null) return `${minimum}–${maximum}`;
  if (minimum !== null) return `${minimum} 이상`;
  if (maximum !== null) return `${maximum} 이하`;
  return null;
}

/**
 * 스키마 default 로 신규 프로필 초깃값을 만든다.
 *
 * main_model 은 의도적으로 제외한다 — 기본값을 DGX 목록의 activeDefault 로 채우기
 * 때문이다. 스키마 default("")를 그대로 쓰면 "서버 기본 모델"이라는 뜻이 되지만,
 * 신규 프로필에는 실제로 서빙 중인 모델을 명시해 두는 편이 읽는 사람에게 정직하다.
 */
const MODEL_FIELDS: ProfileField[] = ['main_model'];

export function buildDefaultConfig(schema: JsonSchema): ProfileConfig {
  const config: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(getProperties(schema))) {
    if (MODEL_FIELDS.includes(key as ProfileField)) continue;
    if (!isRecord(value)) continue;
    if (value.default === undefined) continue;
    config[key] = structuredClone(value.default);
  }
  // id/name 은 스키마에 default 가 없어 비어 있다. 필수 항목 오류로 드러나는 것이 옳다.
  return config as unknown as ProfileConfig;
}
