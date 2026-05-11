import { join } from 'path';

// fs 모듈 모킹 — onModuleInit에서 readFileSync 사용
jest.mock('fs', () => {
  const actual = jest.requireActual<typeof import('fs')>('fs');
  return {
    ...actual,
    readFileSync: jest.fn((filePath: unknown, encoding: unknown) => {
      // 실제 스키마 파일 경로를 위임 처리
      if (typeof filePath === 'string' && filePath.includes('profile-schema.json')) {
        return actual.readFileSync(
          join(__dirname, 'schema', 'profile-schema.json'),
          encoding as BufferEncoding,
        );
      }
      return actual.readFileSync(filePath as string, encoding as BufferEncoding);
    }),
  };
});

import { ProfileSchemaValidator } from './profile-schema.validator';

function makeValidator(): ProfileSchemaValidator {
  const validator = new ProfileSchemaValidator();
  validator.onModuleInit();
  return validator;
}

describe('ProfileSchemaValidator', () => {
  let validator: ProfileSchemaValidator;

  beforeEach(() => {
    validator = makeValidator();
  });

  describe('유효한 config', () => {
    it('필수 필드(id, name, mode)를 모두 포함한 최소 config는 {ok: true}를 반환한다', () => {
      // Arrange
      const config = {
        id: 'my-profile',
        name: 'My Profile',
        mode: 'deterministic',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(true);
    });

    it('선택 필드를 포함한 완전한 config도 {ok: true}를 반환한다', () => {
      // Arrange
      const config = {
        id: 'full-profile',
        name: 'Full Profile',
        mode: 'agentic',
        description: 'A complete profile',
        tools: ['web_search', 'calculator'],
        security_level_required: 'INTERNAL',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(true);
    });

    it('mode가 workflow인 config도 유효하다', () => {
      // Arrange
      const config = {
        id: 'workflow-profile',
        name: 'Workflow Profile',
        mode: 'workflow',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(true);
    });
  });

  describe('필수 필드 누락', () => {
    it('id가 없으면 {ok: false, errors: [...]}를 반환한다', () => {
      // Arrange
      const config = {
        name: 'Profile Without Id',
        mode: 'deterministic',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.errors).toBeDefined();
        expect(result.errors.length).toBeGreaterThan(0);
      }
    });

    it('name이 없으면 {ok: false, errors: [...]}를 반환한다', () => {
      // Arrange
      const config = {
        id: 'no-name-profile',
        mode: 'deterministic',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.errors.length).toBeGreaterThan(0);
      }
    });

    it('mode가 없으면 {ok: false, errors: [...]}를 반환한다', () => {
      // Arrange
      const config = {
        id: 'no-mode-profile',
        name: 'No Mode Profile',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.errors.length).toBeGreaterThan(0);
      }
    });

    it('필수 필드 전부 누락이면 여러 errors가 반환된다', () => {
      // Arrange
      const config = {};

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.errors.length).toBeGreaterThanOrEqual(3);
      }
    });
  });

  describe('잘못된 타입', () => {
    it('mode가 허용되지 않은 enum 값이면 {ok: false}를 반환한다', () => {
      // Arrange
      const config = {
        id: 'invalid-mode',
        name: 'Invalid Mode Profile',
        mode: 'invalid_mode',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.errors.length).toBeGreaterThan(0);
      }
    });

    it('id 패턴이 올바르지 않으면 {ok: false}를 반환한다', () => {
      // Arrange — id는 소문자/숫자/-/_ 만 허용, 최소 2자
      const config = {
        id: 'UPPERCASE_ID',
        name: 'Invalid ID Profile',
        mode: 'deterministic',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(false);
    });

    it('name이 빈 문자열이면 {ok: false}를 반환한다', () => {
      // Arrange
      const config = {
        id: 'valid-id',
        name: '',
        mode: 'deterministic',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.errors.length).toBeGreaterThan(0);
      }
    });

    it('security_level_required가 허용되지 않은 값이면 {ok: false}를 반환한다', () => {
      // Arrange
      const config = {
        id: 'valid-id',
        name: 'Valid Name',
        mode: 'deterministic',
        security_level_required: 'TOP_SECRET',
      };

      // Act
      const result = validator.validate(config);

      // Assert
      expect(result.ok).toBe(false);
    });
  });

  describe('초기화 전 상태', () => {
    it('validateFn이 없으면 validator not initialized 에러를 반환한다', () => {
      // Arrange — onModuleInit을 호출하지 않은 새 인스턴스
      const uninitializedValidator = new ProfileSchemaValidator();

      // Act
      const result = uninitializedValidator.validate({ id: 'test', name: 'Test', mode: 'deterministic' });

      // Assert
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.errors).toContain('validator not initialized');
      }
    });
  });

  describe('getSchema', () => {
    it('초기화 후 스키마 객체를 반환한다', () => {
      // Act
      const schema = validator.getSchema();

      // Assert
      expect(schema).toBeDefined();
      expect(schema).toHaveProperty('type', 'object');
      expect(schema).toHaveProperty('required');
    });
  });
});
