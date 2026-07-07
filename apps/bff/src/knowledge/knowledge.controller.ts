import { Controller, Get, Query, Param, ParseUUIDPipe, NotFoundException, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { KnowledgeService } from './knowledge.service';
import {
  QueryDocumentsDto,
  DocumentsResponseDto,
  DocumentDetailDto,
  KnowledgeStatsDto,
} from './dto/knowledge-query.dto';

/**
 * Knowledge 컨트롤러
 * documents, document_chunks 테이블 조회 API
 */
@Controller('knowledge')
@UseGuards(JwtAuthGuard)
export class KnowledgeController {
  constructor(private readonly knowledgeService: KnowledgeService) {}

  /**
   * 문서 목록 조회
   * GET /bff/knowledge/documents?status=...&source=...&page=1&size=20
   */
  @Get('documents')
  async getDocuments(@Query() query: QueryDocumentsDto): Promise<DocumentsResponseDto> {
    return this.knowledgeService.findDocuments(query);
  }

  /**
   * Knowledge Pipeline 통계
   * GET /bff/knowledge/stats
   */
  @Get('stats')
  async getStats(): Promise<KnowledgeStatsDto> {
    return this.knowledgeService.getStats();
  }

  /**
   * 문서 상세 조회
   * GET /bff/knowledge/documents/:id
   */
  @Get('documents/:id')
  async getDocument(@Param('id', ParseUUIDPipe) id: string): Promise<DocumentDetailDto> {
    const document = await this.knowledgeService.findDocumentById(id);

    if (!document) {
      throw new NotFoundException(`Document with id ${id} not found`);
    }

    return document;
  }
}
// NOTE: 재인덱싱(POST /knowledge/reindex/:id)은 제거됨 — BFF 엔티티가 실제
// job_queue 스키마(queue_name/attempts)와 달라 INSERT 가 항상 실패했고,
// 해당 잡을 소비하는 워커도 없었다. 문서 재처리는 KMS 의 reprocess 경로
// (outbox → document.file_uploaded)가 정도(正道)다.