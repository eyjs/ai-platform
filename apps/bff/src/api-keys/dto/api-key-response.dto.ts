export interface ApiKeyResponseDto {
  id: string;
  name: string;
  preview: string;
  allowed_profiles: string[];
  rate_limit_per_min: number;
  rate_limit_per_day: number;
  security_level_max: 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL';
  is_active: boolean;
  expires_at: string | null;
  revoked_at: string | null;
  rotated_from_id: string | null;
  created_at: string;
  last_used_at: string | null;
}

export interface ApiKeyCreateResponseDto extends ApiKeyResponseDto {
  /** 발급/회전 응답에서만. 1회 노출. */
  plaintext_key: string;
}

export interface ApiKeyAuditEntryDto {
  id: string;
  api_key_id: string;
  actor: string;
  action: 'create' | 'update' | 'revoke' | 'rotate_source' | 'rotate_target';
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}
