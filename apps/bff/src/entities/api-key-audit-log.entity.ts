import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  Index,
} from 'typeorm';

/**
 * api_key_audit_logs 테이블. alembic 010 에서 생성.
 * append-only. whitelist 필드만 before/after 저장.
 */
@Entity('api_key_audit_logs')
export class ApiKeyAuditLog {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Index()
  @Column({ name: 'api_key_id', type: 'uuid' })
  apiKeyId: string;

  @Column({ type: 'varchar', length: 255 })
  actor: string;

  @Column({ type: 'varchar', length: 32 })
  action: 'create' | 'update' | 'revoke' | 'rotate_source' | 'rotate_target';

  @Column({ type: 'jsonb', nullable: true })
  before: Record<string, unknown> | null;

  @Column({ type: 'jsonb', nullable: true })
  after: Record<string, unknown> | null;

  @CreateDateColumn({ name: 'created_at' })
  createdAt: Date;
}
