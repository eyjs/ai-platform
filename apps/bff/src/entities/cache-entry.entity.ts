import { Entity, PrimaryGeneratedColumn, Column, CreateDateColumn, UpdateDateColumn, Index } from 'typeorm';

/**
 * cache_entries 테이블 매핑.
 * apps/api Provider 패턴 캐시 테이블.
 *
 * **Read-only.** BFF는 쿼리만 수행 (Provider 상태 조회용).
 */
@Entity('cache_entries')
export class CacheEntry {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Index()
  @Column({ type: 'varchar', length: 255, nullable: false })
  key: string;

  @Column({ type: 'text', nullable: false })
  value: string;

  @Column({ name: 'expires_at', type: 'timestamptz', nullable: true })
  expiresAt: Date | null;

  @Column({ name: 'provider_type', type: 'varchar', length: 64, nullable: true })
  providerType: string | null;

  @Column({ name: 'provider_id', type: 'varchar', length: 64, nullable: true })
  providerId: string | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;
}