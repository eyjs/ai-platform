import { Entity, PrimaryGeneratedColumn, Column, ManyToOne, JoinColumn, CreateDateColumn } from 'typeorm';
import { Document } from './document.entity';

/**
 * document_chunks 테이블 매핑.
 * apps/api Knowledge Pipeline 소유 테이블.
 *
 * **Read-only.** BFF는 쿼리만 수행.
 */
@Entity('document_chunks')
export class DocumentChunk {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ name: 'document_id', type: 'uuid', nullable: false })
  documentId: string;

  @ManyToOne(() => Document)
  @JoinColumn({ name: 'document_id' })
  document: Document;

  @Column({ name: 'chunk_index', type: 'int', nullable: false })
  chunkIndex: number;

  @Column({ type: 'text', nullable: false })
  content: string;

  @Column({ type: 'vector', nullable: true })
  embedding: number[] | null;

  @Column({ name: 'token_count', type: 'int', default: 0 })
  tokenCount: number;

  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;
}