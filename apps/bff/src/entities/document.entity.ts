import { Entity, PrimaryGeneratedColumn, Column, CreateDateColumn, UpdateDateColumn } from 'typeorm';

/**
 * documents 테이블 매핑.
 * apps/api Knowledge Pipeline 소유 테이블.
 *
 * **Read-only.** BFF는 쿼리만 수행.
 */
@Entity('documents')
export class Document {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'varchar', length: 255, nullable: false })
  title: string;

  @Column({ type: 'text', nullable: true })
  content: string | null;

  @Column({ type: 'varchar', length: 255, nullable: true })
  source: string | null;

  @Column({ type: 'varchar', length: 50, default: 'pending' })
  status: string;

  @Column({ name: 'file_path', type: 'varchar', length: 512, nullable: true })
  filePath: string | null;

  @Column({ name: 'file_size', type: 'int', nullable: true })
  fileSize: number | null;

  @Column({ name: 'mime_type', type: 'varchar', length: 100, nullable: true })
  mimeType: string | null;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;

  @UpdateDateColumn({ name: 'updated_at', type: 'timestamptz' })
  updatedAt: Date;
}