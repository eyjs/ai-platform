import { Entity, PrimaryGeneratedColumn, Column, CreateDateColumn } from 'typeorm';

/**
 * documents 테이블 매핑.
 * apps/api Knowledge Pipeline 소유 테이블. **Read-only.**
 *
 * 실제 컬럼: id, external_id, title, file_name, file_hash, domain_code,
 *           security_level, source_url, metadata, created_at, tenant_id.
 * (content는 document_chunks에, status/file_size/mime_type 컬럼은 없음 — 선언 금지.)
 */
@Entity('documents')
export class Document {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'varchar', nullable: false })
  title: string;

  @Column({ name: 'file_name', type: 'varchar', nullable: true })
  fileName: string | null;

  @Column({ name: 'domain_code', type: 'varchar', nullable: true })
  domainCode: string | null;

  @Column({ name: 'security_level', type: 'varchar', nullable: true })
  securityLevel: string | null;

  @Column({ name: 'source_url', type: 'varchar', nullable: true })
  sourceUrl: string | null;

  @Column({ type: 'jsonb', nullable: true })
  metadata: Record<string, unknown> | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;
}
