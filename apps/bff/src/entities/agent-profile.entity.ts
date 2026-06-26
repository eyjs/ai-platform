import { Entity, PrimaryColumn, Column, CreateDateColumn, UpdateDateColumn } from 'typeorm';

@Entity('agent_profiles')
export class AgentProfile {
  @PrimaryColumn({ type: 'text' })
  id: string;

  @Column({ type: 'text' })
  name: string;

  @Column({ type: 'text', nullable: true })
  description: string | null;

  // NOTE: agent_profiles 테이블엔 mode 컬럼이 없다(api/alembic 소유). mode는 config(JSONB)에 저장.
  // 가짜 컬럼을 두면 TypeORM이 SELECT "mode" → Postgres가 mode() 집계로 해석해 500.

  @Column({ type: 'jsonb', nullable: true })
  config: Record<string, unknown> | null;

  @Column({ name: 'is_active', default: true })
  isActive: boolean;

  @CreateDateColumn({ name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at' })
  updatedAt: Date;
}
