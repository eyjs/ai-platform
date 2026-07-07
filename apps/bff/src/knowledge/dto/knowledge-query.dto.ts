import { IsOptional, IsString, IsInt, Min, Max } from 'class-validator';
import { Transform, Type } from 'class-transformer';

/**
 * 문서 목록 조회 DTO. documents 실제 컬럼(domain_code, security_level) 기준 필터.
 */
export class QueryDocumentsDto {
  @IsOptional()
  @IsString()
  domainCode?: string;

  @IsOptional()
  @IsString()
  securityLevel?: string;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Type(() => Number)
  @Transform(({ value }) => value || 1)
  page?: number = 1;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(100)
  @Type(() => Number)
  @Transform(({ value }) => value || 20)
  size?: number = 20;
}

export class DocumentsResponseDto {
  items: DocumentItemDto[];
  total: number;
  page: number;
  size: number;
}

/** 문서 항목 — documents 실제 컬럼. */
export class DocumentItemDto {
  id: string;
  title: string;
  fileName: string | null;
  domainCode: string | null;
  securityLevel: string | null;
  sourceUrl: string | null;
  createdAt: string;
}

/** 문서 상세 — content는 document_chunks 연결, chunkCount 포함. */
export class DocumentDetailDto extends DocumentItemDto {
  content: string | null;
  chunkCount: number;
  metadata: Record<string, unknown> | null;
}

/** Knowledge 통계 — documents엔 status 컬럼이 없어 도메인/보안등급 분포로. */
export class KnowledgeStatsDto {
  totalDocuments: number;
  totalChunks: number;
  avgChunksPerDocument: number;
  documentsByDomain: { domain: string; count: number }[];
  documentsBySecurityLevel: { level: string; count: number }[];
}
