import { Injectable, OnModuleInit } from '@nestjs/common';
import { readFileSync } from 'fs';
import { join } from 'path';

export interface ValidationOk {
  ok: true;
}
export interface ValidationFail {
  ok: false;
  errors: string[];
}
export type ValidationResult = ValidationOk | ValidationFail;

@Injectable()
export class ProfileSchemaValidator implements OnModuleInit {
  private schema: Record<string, unknown> = {};
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private validateFn: any = null;

  onModuleInit(): void {
    const schemaPath = join(__dirname, 'schema', 'profile-schema.json');
    const raw = readFileSync(schemaPath, 'utf-8');
    this.schema = JSON.parse(raw) as Record<string, unknown>;

    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const AjvModule = require('ajv');
    const AjvCtor = AjvModule.default || AjvModule;

    const ajv = new AjvCtor({ allErrors: true, strict: false });
    this.validateFn = ajv.compile(this.schema);
  }

  validate(config: unknown): ValidationResult {
    if (!this.validateFn) {
      return { ok: false, errors: ['validator not initialized'] };
    }
    const valid = this.validateFn(config);
    if (valid) return { ok: true };
    const errors = ((this.validateFn.errors ?? []) as Array<{ instancePath?: string; message?: string }>).map(
      (e) => `${e.instancePath || '/'} ${e.message ?? 'invalid'}`,
    );
    return { ok: false, errors };
  }

  getSchema(): Record<string, unknown> {
    return this.schema;
  }
}
