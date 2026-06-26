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
    const { domainCode, securityLevel, page = 1, size = 20 } = query;

    const queryBuilder = this.documentRepo.createQueryBuilder('doc');

    if (domainCode) {
      queryBuilder.andWhere('doc.domainCode = :domainCode', { domainCode });
    }

    if (securityLevel) {
      queryBuilder.andWhere('doc.securityLevel = :securityLevel', { securityLevel });
    }

    queryBuilder
      .orderBy('doc.createdAt', 'DESC')
      .skip((page - 1) * size)
      .take(size);

    const [documents, total] = await queryBuilder.getManyAndCount();

    const items: DocumentItemDto[] = documents.map((doc) => ({
      id: doc.id,
      title: doc.title,
      fileName: doc.fileName,
      domainCode: doc.domainCode,
      securityLevel: doc.securityLevel,
      sourceUrl: doc.sourceUrl,
      createdAt: doc.createdAt.toISOString(),
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

    // 청크 수 + content(청크를 순서대로 이어붙임, 상한 50). 실제 컬럼만 사용하는 raw 쿼리.
    const chunkCount = await this.chunkRepo.count({ where: { documentId: id } });
    const chunkRows: Array<{ content: string }> = await this.documentRepo.manager
      .query(
        `SELECT content FROM document_chunks WHERE document_id = $1 ORDER BY chunk_index ASC LIMIT 50`,
        [id],
      )
      .catch(() => []);
    const content = chunkRows.map((r) => r.content).join('\n\n') || null;

    return {
      id: document.id,
      title: document.title,
      fileName: document.fileName,
      domainCode: document.domainCode,
      securityLevel: document.securityLevel,
      sourceUrl: document.sourceUrl,
      createdAt: document.createdAt.toISOString(),
      content,
      chunkCount,
      metadata: document.metadata,
    };
  }

  /**
   * Knowledge Pipeline 통계
   */
  async getStats(): Promise<KnowledgeStatsDto> {
    const manager = this.documentRepo.manager;

    // documents엔 status 컬럼이 없다 → 도메인/보안등급 분포로 집계.
    const docResult = await manager
      .query(`SELECT COUNT(*)::int AS total_documents FROM documents`)
      .catch(() => [{ total_documents: 0 }]);
    const chunkResult = await manager
      .query(`SELECT COUNT(*)::int AS total_chunks FROM document_chunks`)
      .catch(() => [{ total_chunks: 0 }]);
    const domainResult = await manager
      .query(
        `SELECT COALESCE(domain_code, 'unknown') AS domain, COUNT(*)::int AS count
         FROM documents GROUP BY domain_code ORDER BY count DESC LIMIT 10`,
      )
      .catch(() => []);
    const securityResult = await manager
      .query(
        `SELECT COALESCE(security_level, 'unknown') AS level, COUNT(*)::int AS count
         FROM documents GROUP BY security_level ORDER BY count DESC`,
      )
      .catch(() => []);

    const totalDocuments = Number(docResult[0].total_documents);
    const totalChunks = Number(chunkResult[0].total_chunks);

    return {
      totalDocuments,
      totalChunks,
      avgChunksPerDocument: totalDocuments > 0 ? Math.round((totalChunks / totalDocuments) * 10) / 10 : 0,
      documentsByDomain: domainResult.map((row: Record<string, unknown>) => ({
        domain: String(row.domain),
        count: Number(row.count),
      })),
      documentsBySecurityLevel: securityResult.map((row: Record<string, unknown>) => ({
        level: String(row.level),
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