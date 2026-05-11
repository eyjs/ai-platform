import { describe, it, expect } from 'vitest';
import { cn } from './cn';

describe('cn()', () => {
  it('단일 클래스를 반환한다', () => {
    expect(cn('p-4')).toBe('p-4');
  });

  it('여러 클래스를 공백으로 병합한다', () => {
    expect(cn('flex', 'items-center', 'gap-2')).toBe('flex items-center gap-2');
  });

  it('falsy 값을 제거한다', () => {
    expect(cn('p-4', false && 'hidden', undefined, null, '')).toBe('p-4');
  });

  it('조건부 클래스를 올바르게 처리한다', () => {
    const isActive = true;
    const isDisabled = false;
    expect(cn('btn', isActive && 'btn-active', isDisabled && 'btn-disabled')).toBe('btn btn-active');
  });

  it('tailwind 충돌 시 마지막 값이 우선한다 (p-4 vs p-2)', () => {
    expect(cn('p-4', 'p-2')).toBe('p-2');
  });

  it('tailwind 충돌 시 마지막 값이 우선한다 (text-sm vs text-lg)', () => {
    expect(cn('text-sm', 'text-lg')).toBe('text-lg');
  });

  it('배열 인자를 처리한다', () => {
    expect(cn(['flex', 'items-center'])).toBe('flex items-center');
  });

  it('인자 없이 호출하면 빈 문자열을 반환한다', () => {
    expect(cn()).toBe('');
  });
});
