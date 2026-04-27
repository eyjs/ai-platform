import { Entity, PrimaryGeneratedColumn, Column, CreateDateColumn, UpdateDateColumn, Index } from 'typeorm';

/**
 * job_queue 테이블 매핑.
 * apps/api 작업 큐 테이블.
 *
 * **읽기 + INSERT 가능.** BFF에서 재인덱싱 요청 시 INSERT 수행.
 */
@Entity('job_queue')
export class JobQueue {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'job_type', type: 'varchar', length: 64, nullable: false })
  jobType: string;

  @Column({ type: 'json', nullable: true })
  payload: Record<string, unknown> | null;

  @Index()
  @Column({ type: 'varchar', length: 32, default: 'pending' })
  status: string;

  @Column({ type: 'int', default: 0 })
  priority: number;

  @Column({ name: 'retry_count', type: 'int', default: 0 })
  retryCount: number;

  @Column({ name: 'max_retries', type: 'int', default: 3 })
  maxRetries: number;

  @Column({ name: 'scheduled_at', type: 'timestamptz', nullable: true })
  scheduledAt: Date | null;

  @Column({ name: 'started_at', type: 'timestamptz', nullable: true })
  startedAt: Date | null;

  @Column({ name: 'completed_at', type: 'timestamptz', nullable: true })
  completedAt: Date | null;

  @Column({ name: 'error_message', type: 'text', nullable: true })
  errorMessage: string | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;
}