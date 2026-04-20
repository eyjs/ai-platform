import { Entity, PrimaryGeneratedColumn, Column, Index, ManyToOne, JoinColumn } from 'typeorm';
import { ApiKey } from './api-key.entity';

/**
 * api_request_logs 테이블 매핑.
 * alembic 011 에서 생성.
 *
 * **Read-only.** api 쪽 RequestLogService 가 insert 담당. BFF 는 쿼리만.
 */
@Entity('api_request_logs')
export class ApiRequestLog {
  @PrimaryGeneratedColumn({ type: 'bigint' })
  id: string;

  @Index()
  @Column({ type: 'timestamptz' })
  ts: Date;

  @Column({ name: 'api_key_id', type: 'uuid', nullable: true })
  apiKeyId: string | null;

  @ManyToOne(() => ApiKey, { nullable: true })
  @JoinColumn({ name: 'api_key_id' })
  apiKey: ApiKey | null;

  @Column({ name: 'profile_id', type: 'varchar', length: 255, nullable: true })
  profileId: string | null;

  @Column({ name: 'provider_id', type: 'varchar', length: 64, nullable: true })
  providerId: string | null;

  @Column({ name: 'status_code', type: 'int' })
  statusCode: number;

  @Column({ name: 'latency_ms', type: 'int' })
  latencyMs: number;

  @Column({ name: 'prompt_tokens', type: 'int', default: 0 })
  promptTokens: number;

  @Column({ name: 'completion_tokens', type: 'int', default: 0 })
  completionTokens: number;

  @Column({ name: 'cache_hit', type: 'boolean', default: false })
  cacheHit: boolean;

  @Column({ name: 'error_code', type: 'varchar', length: 64, nullable: true })
  errorCode: string | null;

  @Column({ name: 'request_preview', type: 'text', nullable: true })
  requestPreview: string | null;

  @Column({ name: 'response_preview', type: 'text', nullable: true })
  responsePreview: string | null;
}
