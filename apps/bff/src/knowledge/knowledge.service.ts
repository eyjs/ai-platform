import { Injectable } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Document } from '../entities/document.entity';
import { DocumentChunk } from '../entities/document-chunk.entity';
import { JobQueue } from '../entities/job-queue.entity';
import {
  QueryDocumentsDto,
  DocumentsResponseDto,
  DocumentItemDto,
  DocumentDetailDto,
  KnowledgeStatsDto,
  ReindexResponseDto,
} from './dto/knowledge-query.dto';

/**
 * Knowledge 서비스
 * documents, document_chunks, job_queue 테이블 조회
 */
@Injectable()
export class KnowledgeService {
  constructor(
    @InjectRepository(Document)
    private readonly documentRepo: Repository<Document>,
    @InjectRepository(DocumentChunk)
    private readonly chunkRepo: Repository<DocumentChunk>,
    @InjectRepository(JobQueue)
    private readonly jobQueueRepo: Repository<JobQueue>,
  ) {}

  /**
   * 문서 목록 조회
   */
  async findDocuments(query: QueryDocumentsDto): Promise<DocumentsResponseDto> {
    const { status, source, page = 1, size = 20 } = query;

    const queryBuilder = this.documentRepo.createQueryBuilder('doc');

    if (status) {
      queryBuilder.andWhere('doc.status = :status', { status });
    }

    if (source) {
      queryBuilder.andWhere('doc.source = :source', { source });
    }

    queryBuilder
      .orderBy('doc.createdAt', 'DESC')
      .skip((page - 1) * size)
      .take(size);

    const [documents, total] = await queryBuilder.getManyAndCount();

    const items: DocumentItemDto[] = documents.map((doc) => ({
      id: doc.id,
      title: doc.title,
      source: doc.source,
      status: doc.status,
      fileSize: doc.fileSize,
      mimeType: doc.mimeType,
      createdAt: doc.createdAt.toISOString(),
      updatedAt: doc.updatedAt.toISOString(),
    }));

    return {
      items,
      total,
      page,
      size,
    };
  }

  /**
   * 문서 상세 조회 (청크 수 포함)
   */
  async findDocumentById(id: string): Promise<DocumentDetailDto | null> {
    const document = await this.documentRepo.findOne({ where: { id } });

    if (!document) {
      return null;
    }

    // 청크 수 조회
    const chunkCount = await this.chunkRepo.count({ where: { documentId: id } });

    return {
      id: document.id,
      title: document.title,
      content: document.content,
      source: document.source,
      status: document.status,
      filePath: document.filePath,
      fileSize: document.fileSize,
      mimeType: document.mimeType,
      createdAt: document.createdAt.toISOString(),
      updatedAt: document.updatedAt.toISOString(),
      chunkCount,
    };
  }

  /**
   * Knowledge Pipeline 통계
   */
  async getStats(): Promise<KnowledgeStatsDto> {
    const manager = this.documentRepo.manager;

    // 기본 통계
    const statsResult = await manager.query(
      `SELECT
         COUNT(*)::int AS total_documents,
         SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END)::int AS pending_documents,
         SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)::int AS completed_documents,
         SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)::int AS failed_documents
       FROM documents`,
    ).catch(() => [{ total_documents: 0, pending_documents: 0, completed_documents: 0, failed_documents: 0 }]);

    // 총 청크 수
    const chunkResult = await manager.query(
      `SELECT COUNT(*)::int AS total_chunks FROM document_chunks`,
    ).catch(() => [{ total_chunks: 0 }]);

    // 상태별 문서 수
    const statusResult = await manager.query(
      `SELECT status, COUNT(*)::int AS count
       FROM documents
       GROUP BY status
       ORDER BY count DESC`,
    ).catch(() => []);

    // 소스별 문서 수
    const sourceResult = await manager.query(
      `SELECT COALESCE(source, 'unknown') AS source, COUNT(*)::int AS count
       FROM documents
       GROUP BY source
       ORDER BY count DESC
       LIMIT 10`,
    ).catch(() => []);

    const stats = statsResult[0];
    const totalDocuments = Number(stats.total_documents);
    const totalChunks = Number(chunkResult[0].total_chunks);

    return {
      totalDocuments,
      pendingDocuments: Number(stats.pending_documents),
      completedDocuments: Number(stats.completed_documents),
      failedDocuments: Number(stats.failed_documents),
      totalChunks,
      avgChunksPerDocument: totalDocuments > 0 ? Math.round((totalChunks / totalDocuments) * 10) / 10 : 0,
      documentsByStatus: statusResult.map((row: Record<string, unknown>) => ({
        status: String(row.status),
        count: Number(row.count),
      })),
      documentsBySource: sourceResult.map((row: Record<string, unknown>) => ({
        source: String(row.source),
        count: Number(row.count),
      })),
    };
  }

  /**
   * 재인덱싱 요청
   */
  async requestReindex(documentId: string): Promise<ReindexResponseDto> {
    // 문서 존재 확인
    const document = await this.documentRepo.findOne({ where: { id: documentId } });

    if (!document) {
      throw new Error(`Document with id ${documentId} not found`);
    }

    // job_queue에 재인덱싱 작업 추가
    const job = this.jobQueueRepo.create({
      jobType: 'reindex_document',
      payload: {
        document_id: documentId,
        title: document.title,
      },
      status: 'pending',
      priority: 10,
      scheduledAt: new Date(),
    });

    const savedJob = await this.jobQueueRepo.save(job);

    return {
      jobId: savedJob.id,
      documentId,
      status: 'queued',
      message: `Reindexing job queued for document: ${document.title}`,
    };
  }
}