export type SecurityLevel = 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL';

export interface ApiKey {
  id: string;
  name: string;
  preview: string;
  allowed_profiles: string[];
  rate_limit_per_min: number;
  rate_limit_per_day: number;
  security_level_max: SecurityLevel;
  is_active: boolean;
  expires_at: string | null;
  revoked_at: string | null;
  rotated_from_id: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreateResponse extends ApiKey {
  plaintext_key: string;
}

export interface ApiKeyCreateRequest {
  name: string;
  allowed_profiles: string[];
  rate_limit_per_min: number;
  rate_limit_per_day: number;
  security_level_max: SecurityLevel;
  expires_at?: string | null;
}

export interface ApiKeyUpdateRequest {
  name?: string;
  allowed_profiles?: string[];
  rate_limit_per_min?: number;
  rate_limit_per_day?: number;
  security_level_max?: SecurityLevel;
  expires_at?: string | null;
  is_active?: boolean;
}

export interface ApiKeyAuditEntry {
  id: string;
  api_key_id: string;
  actor: string;
  action: 'create' | 'update' | 'revoke' | 'rotate_source' | 'rotate_target';
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}
