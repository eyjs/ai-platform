import { Injectable, OnModuleInit } from '@nestjs/common';
import { readFileSync } from 'fs';
import { join } from 'path';
import Ajv, { ValidateFunction } from 'ajv';
import addFormats from 'ajv-formats';

export interface ValidationOk {
  ok: true;
}
export interface ValidationFail {
  ok: false;
  errors: string[];
}
export type ValidationResult = ValidationOk | ValidationFail;

/**
 * Profile JSON Schema validator (singleton).
 *
 * - 모듈 초기화 시 1회 fs.readFileSync 로 schema 로드 → ajv 컴파일 캐싱
 * - 요청 경로에서는 fs I/O 금지
 * - 스키마 정본: .pipeline/contracts/profile-yaml-schema.json
 * - BFF 런타임 복사본: apps/bff/src/profiles/schema/profile-schema.json (nest-cli assets 로 dist 번들)
 */
@Injectable()
export class ProfileSchemaValidator implements OnModuleInit {
  private schema: Record<string, unknown> = {};
  private validateFn: ValidateFunction | null = null;

  onModuleInit(): void {
    const schemaPath = join(__dirname, 'schema', 'profile-schema.json');
    const raw = readFileSync(schemaPath, 'utf-8');
    this.schema = JSON.parse(raw) as Record<string, unknown>;

    const ajv = new Ajv({ allErrors: true, strict: false });
    addFormats(ajv);
    this.validateFn = ajv.compile(this.schema);
  }

  validate(config: unknown): ValidationResult {
    if (!this.validateFn) {
      return { ok: false, errors: ['validator not initialized'] };
    }
    const valid = this.validateFn(config);
    if (valid) return { ok: true };
    const errors = (this.validateFn.errors ?? []).map(
      (e) => `${e.instancePath || '/'} ${e.message ?? 'invalid'}`,
    );
    return { ok: false, errors };
  }

  getSchema(): Record<string, unknown> {
    return this.schema;
  }
}
