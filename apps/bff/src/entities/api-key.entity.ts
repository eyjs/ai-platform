import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  Index,
} from 'typeorm';

/**
 * api_keys 테이블 매핑.
 * 마이그레이션: alembic 002 + 010 (rate_limit_per_day, rotated_from_id, revoked_at).
 *
 * **Task 006 소유 엔티티.** 다른 모듈은 수정 금지, import 만 허용.
 */
@Entity('api_keys')
export class ApiKey {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Index()
  @Column({ name: 'key_hash', type: 'varchar', length: 64, unique: true })
  keyHash: string;

  @Column({ type: 'varchar', length: 255 })
  name: string;

  @Column({ name: 'user_id', type: 'varchar', length: 255, default: '' })
  userId: string;

  @Column({ name: 'user_role', type: 'varchar', length: 20, default: 'VIEWER' })
  userRole: string;

  @Column({ name: 'security_level_max', type: 'varchar', length: 20, default: 'PUBLIC' })
  securityLevelMax: 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL';

  @Column({ name: 'allowed_profiles', type: 'text', array: true, default: () => "'{}'" })
  allowedProfiles: string[];

  @Column({ name: 'rate_limit_per_min', type: 'int', default: 60 })
  rateLimitPerMin: number;

  @Column({ name: 'rate_limit_per_day', type: 'int', default: 10000 })
  rateLimitPerDay: number;

  @Column({ name: 'is_active', type: 'boolean', default: true })
  isActive: boolean;

  @Column({ name: 'expires_at', type: 'timestamptz', nullable: true })
  expiresAt: Date | null;

  @Column({ name: 'rotated_from_id', type: 'uuid', nullable: true })
  rotatedFromId: string | null;

  @Column({ name: 'revoked_at', type: 'timestamptz', nullable: true })
  revokedAt: Date | null;

  @CreateDateColumn({ name: 'created_at' })
  createdAt: Date;

  @Column({ name: 'last_used_at', type: 'timestamptz', nullable: true })
  lastUsedAt: Date | null;
}
