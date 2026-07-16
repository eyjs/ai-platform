/**
 * 라운드트립 회귀 테스트.
 *
 * 폼이 프로필을 열었다가 저장할 때 키가 하나라도 사라지면 그것은 데이터 손실이고,
 * 우리가 대체한 YAML 편집기보다 나쁘다. fortune-saju 는 가장 복잡한 실제 프로필이라
 * (hybrid_triggers / intent_hints / memory_scopes[local,user] / context_adapter /
 * cache_padding_text / empty_response_fallback) 회귀 감시 대상으로 삼는다.
 *
 * fixtures/fortune-saju.yaml 은 apps/api/seeds/profiles/fortune-saju.yaml 의 사본이다
 * (앱 경계를 넘어 import 하지 않기 위해 복사해 둔다).
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { getProfileField, parseProfileYaml, serializeProfileYaml, setProfileField } from '../profile-yaml';
import type { ProfileConfig } from '@/types/profile';

const FIXTURE = readFileSync(join(__dirname, 'fixtures/fortune-saju.yaml'), 'utf8');

function parseOrThrow(text: string): ProfileConfig {
  const { config, error } = parseProfileYaml(text);
  if (!config) throw new Error(`파싱 실패: ${error}`);
  return config;
}

describe('fortune-saju 라운드트립', () => {
  it('YAML → 폼 상태 → YAML 이 값을 잃지 않는다', () => {
    const original = parseOrThrow(FIXTURE);
    const roundTripped = parseOrThrow(serializeProfileYaml(original));

    expect(roundTripped).toEqual(original);
  });

  it('실제 프로필의 모든 최상위 키가 보존된다', () => {
    const original = parseOrThrow(FIXTURE);
    const roundTripped = parseOrThrow(serializeProfileYaml(original));

    expect(Object.keys(roundTripped).sort()).toEqual(Object.keys(original).sort());
  });

  it('복합 필드가 구조까지 그대로 유지된다', () => {
    const original = parseOrThrow(FIXTURE);
    const result = parseOrThrow(serializeProfileYaml(original));

    // 폼이 렌더하는 조건부/중첩 필드들
    expect(result.mode).toBe('hybrid');
    expect(result.memory_scopes).toEqual(['local', 'user']);
    expect(result.context_adapter).toBe('saju');
    expect(result.workflow_id).toBeNull(); // null 은 명시적 값이므로 사라지면 안 된다
    expect(result.hybrid_triggers).toHaveLength(2);
    expect(result.hybrid_triggers?.[0].workflow_id).toBe('saju_compatibility');
    expect(result.hybrid_triggers?.[0].intent_types).toEqual(['COMPATIBILITY', 'REPORT_COMPATIBILITY']);
    expect(result.intent_hints).toHaveLength(10);
    expect(result.intent_hints?.[0]).toEqual({
      name: 'COMPATIBILITY',
      patterns: ['궁합', '결혼운', '인연', '커플', '상성'],
      description: '두 사람 사이의 궁합 분석 요청',
    });
    expect(result.tools).toEqual(original.tools);
    expect(result.system_prompt).toBe(original.system_prompt);
    expect(result.cache_padding_text).toBe(original.cache_padding_text);
    expect(result.empty_response_fallback).toBe('흐음, 방금 건 잘 못 들었어. 다시 말해줄래? 🐾');
  });

  it('폼에서 한 필드를 고쳐도 나머지는 그대로다', () => {
    const original = parseOrThrow(FIXTURE);
    const edited = setProfileField(original, 'name', '묘묘');
    const result = parseOrThrow(serializeProfileYaml(edited));

    expect(result.name).toBe('묘묘');
    expect(Object.keys(result).sort()).toEqual(Object.keys(original).sort());

    // name 외에는 어떤 값도 달라지지 않아야 한다.
    for (const key of Object.keys(original) as (keyof ProfileConfig)[]) {
      if (key === 'name') continue;
      expect(getProfileField(result, key)).toEqual(getProfileField(original, key));
    }
  });

  it('폼이 렌더하지 않는 미지의 키도 저장 시 살아남는다', () => {
    const withUnknown = parseOrThrow(`${FIXTURE}\ncache_config:\n  enabled: true\n  ttl_seconds: 900\nfuture_key: "폼이 모르는 값"\n`);
    const result = parseOrThrow(serializeProfileYaml(withUnknown));

    expect(result.cache_config).toEqual({ enabled: true, ttl_seconds: 900 });
    expect(getProfileField(result, 'future_key' as keyof ProfileConfig)).toBe('폼이 모르는 값');
  });

  it('값을 undefined 로 지우면 키가 사라지고, null 은 남는다', () => {
    const original = parseOrThrow(FIXTURE);

    const cleared = parseOrThrow(serializeProfileYaml(setProfileField(original, 'description', undefined)));
    expect('description' in cleared).toBe(false);

    const nulled = parseOrThrow(serializeProfileYaml(setProfileField(original, 'context_adapter', null)));
    expect(nulled.context_adapter).toBeNull();
  });
});

describe('YAML 파싱 실패 처리', () => {
  it('문법 오류는 줄 번호와 함께 보고된다', () => {
    const { config, error } = parseProfileYaml('id: a\n  bad: [unclosed\n');
    expect(config).toBeNull();
    expect(error).toContain('YAML 문법 오류');
  });

  it('최상위가 매핑이 아니면 거부한다', () => {
    const { config, error } = parseProfileYaml('- 리스트\n- 항목\n');
    expect(config).toBeNull();
    expect(error).toContain('키-값 매핑');
  });
});
