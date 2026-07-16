import eslint from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    // 빌드 산출물 / 의존성 — 소스가 아니므로 린트 대상 아님
    ignores: ['dist/**', 'node_modules/**', 'eslint.config.mjs'],
  },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // ajv는 dual ESM/CJS 패키지라 default export 상호운용을 위해 런타임 require()가 필요하다.
    // 원 작성자가 이미 파일 내에 no-var-requires disable을 달아 두었으나, typescript-eslint v8에서
    // 해당 룰이 no-require-imports로 개명되면서 주석이 무효화됐다. 파일은 다른 작업자 소유라
    // 여기서 파일 단위로만 예외 처리한다. (소유자가 파일 내 주석을 개명된 룰명으로 갱신하면 이 블록 삭제 가능)
    files: ['src/profiles/profile-schema.validator.ts'],
    rules: {
      '@typescript-eslint/no-require-imports': 'off',
    },
  },
  {
    languageOptions: {
      globals: {
        // NestJS는 Node 런타임. no-undef가 process/console 등을 오인하지 않도록 선언
        process: 'readonly',
        console: 'readonly',
        Buffer: 'readonly',
        __dirname: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
      },
    },
  },
);
