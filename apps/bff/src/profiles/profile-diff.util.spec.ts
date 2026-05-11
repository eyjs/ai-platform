import { computeDiff, DiffResult } from './profile-diff.util';

describe('computeDiff', () => {
  describe('동일 객체', () => {
    it('동일한 객체는 빈 diff를 반환한다', () => {
      const obj = { a: 1, b: 'hello', c: true };
      const result: DiffResult = computeDiff(obj, obj);
      expect(result.added).toEqual({});
      expect(result.removed).toEqual({});
      expect(result.changed).toEqual({});
    });

    it('동일한 내용의 다른 객체는 빈 diff를 반환한다', () => {
      const before = { x: 42, y: 'test' };
      const after = { x: 42, y: 'test' };
      const result = computeDiff(before, after);
      expect(result.added).toEqual({});
      expect(result.removed).toEqual({});
      expect(result.changed).toEqual({});
    });
  });

  describe('키 추가', () => {
    it('after에 새로운 키가 추가되면 added에 포함된다', () => {
      const before = { a: 1 };
      const after = { a: 1, b: 2 };
      const result = computeDiff(before, after);
      expect(result.added).toEqual({ b: 2 });
      expect(result.removed).toEqual({});
      expect(result.changed).toEqual({});
    });

    it('여러 키가 추가되면 모두 added에 포함된다', () => {
      const before = { a: 1 };
      const after = { a: 1, b: 2, c: 3, d: 'new' };
      const result = computeDiff(before, after);
      expect(result.added).toEqual({ b: 2, c: 3, d: 'new' });
    });
  });

  describe('키 삭제', () => {
    it('before에 있던 키가 after에 없으면 removed에 포함된다', () => {
      const before = { a: 1, b: 2 };
      const after = { a: 1 };
      const result = computeDiff(before, after);
      expect(result.removed).toEqual({ b: 2 });
      expect(result.added).toEqual({});
      expect(result.changed).toEqual({});
    });

    it('여러 키가 삭제되면 모두 removed에 포함된다', () => {
      const before = { a: 1, b: 2, c: 3 };
      const after = { a: 1 };
      const result = computeDiff(before, after);
      expect(result.removed).toEqual({ b: 2, c: 3 });
    });
  });

  describe('값 변경', () => {
    it('값이 변경되면 changed에 before/after가 포함된다', () => {
      const before = { a: 1, b: 'old' };
      const after = { a: 1, b: 'new' };
      const result = computeDiff(before, after);
      expect(result.changed).toEqual({ b: { before: 'old', after: 'new' } });
      expect(result.added).toEqual({});
      expect(result.removed).toEqual({});
    });

    it('숫자 값이 변경되면 changed에 포함된다', () => {
      const before = { count: 10 };
      const after = { count: 20 };
      const result = computeDiff(before, after);
      expect(result.changed).toEqual({ count: { before: 10, after: 20 } });
    });

    it('boolean 값이 변경되면 changed에 포함된다', () => {
      const before = { active: false };
      const after = { active: true };
      const result = computeDiff(before, after);
      expect(result.changed).toEqual({ active: { before: false, after: true } });
    });
  });

  describe('중첩 객체', () => {
    it('중첩 객체의 변경은 dot-notation 경로로 changed에 포함된다', () => {
      const before = { nested: { key: 'old' } };
      const after = { nested: { key: 'new' } };
      const result = computeDiff(before, after);
      expect(result.changed).toEqual({ 'nested.key': { before: 'old', after: 'new' } });
    });

    it('중첩 객체의 새 키는 dot-notation 경로로 added에 포함된다', () => {
      const before = { nested: { a: 1 } };
      const after = { nested: { a: 1, b: 2 } };
      const result = computeDiff(before, after);
      expect(result.added).toEqual({ 'nested.b': 2 });
    });

    it('중첩 객체의 삭제된 키는 dot-notation 경로로 removed에 포함된다', () => {
      const before = { nested: { a: 1, b: 2 } };
      const after = { nested: { a: 1 } };
      const result = computeDiff(before, after);
      expect(result.removed).toEqual({ 'nested.b': 2 });
    });

    it('3단계 이상 중첩도 올바르게 처리된다', () => {
      const before = { a: { b: { c: 'deep' } } };
      const after = { a: { b: { c: 'changed' } } };
      const result = computeDiff(before, after);
      expect(result.changed).toEqual({ 'a.b.c': { before: 'deep', after: 'changed' } });
    });
  });

  describe('빈 객체', () => {
    it('둘 다 빈 객체이면 빈 diff를 반환한다', () => {
      const result = computeDiff({}, {});
      expect(result.added).toEqual({});
      expect(result.removed).toEqual({});
      expect(result.changed).toEqual({});
    });

    it('before가 빈 객체이면 after의 모든 키가 added에 포함된다', () => {
      const result = computeDiff({}, { a: 1, b: 2 });
      expect(result.added).toEqual({ a: 1, b: 2 });
      expect(result.removed).toEqual({});
      expect(result.changed).toEqual({});
    });

    it('after가 빈 객체이면 before의 모든 키가 removed에 포함된다', () => {
      const result = computeDiff({ a: 1, b: 2 }, {});
      expect(result.removed).toEqual({ a: 1, b: 2 });
      expect(result.added).toEqual({});
      expect(result.changed).toEqual({});
    });

    it('before가 null이면 after의 모든 키가 added에 포함된다', () => {
      const result = computeDiff(null, { a: 1 });
      expect(result.added).toEqual({ a: 1 });
      expect(result.removed).toEqual({});
    });

    it('after가 null이면 before의 모든 키가 removed에 포함된다', () => {
      const result = computeDiff({ a: 1 }, null);
      expect(result.removed).toEqual({ a: 1 });
      expect(result.added).toEqual({});
    });

    it('before와 after 둘 다 undefined이면 빈 diff를 반환한다', () => {
      const result = computeDiff(undefined, undefined);
      expect(result.added).toEqual({});
      expect(result.removed).toEqual({});
      expect(result.changed).toEqual({});
    });
  });
});
