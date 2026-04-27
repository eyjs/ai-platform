import { Controller, Get, Post, Query, Param, ParseUUIDPipe, NotFoundException, UseGuards, BadRequestException } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { KnowledgeService } from './knowledge.service';
import {
  QueryDocumentsDto,
  DocumentsResponseDto,
  DocumentDetailDto,
  KnowledgeStatsDto,
  ReindexResponseDto,
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

  /**
   * 재인덱싱 요청
   * POST /bff/knowledge/reindex/:id
   */
  @Post('reindex/:id')
  async requestReindex(@Param('id', ParseUUIDPipe) id: string): Promise<ReindexResponseDto> {
    try {
      return await this.knowledgeService.requestReindex(id);
    } catch (error) {
      throw new BadRequestException(error instanceof Error ? error.message : 'Reindex request failed');
    }
  }
}