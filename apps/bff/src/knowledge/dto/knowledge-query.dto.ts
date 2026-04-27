import { IsOptional, IsString, IsInt, Min, Max } from 'class-validator';
import { Transform, Type } from 'class-transformer';

/**
 * 문서 목록 조회 DTO
 */
export class QueryDocumentsDto {
  @IsOptional()
  @IsString()
  status?: string;

  @IsOptional()
  @IsString()
  source?: string;

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

/**
 * 문서 목록 응답 DTO
 */
export class DocumentsResponseDto {
  items: DocumentItemDto[];
  total: number;
  page: number;
  size: number;
}

/**
 * 문서 항목 DTO
 */
export class DocumentItemDto {
  id: string;
  title: string;
  source: string | null;
  status: string;
  fileSize: number | null;
  mimeType: string | null;
  createdAt: string;
  updatedAt: string;
}

/**
 * 문서 상세 응답 DTO (청크 수 포함)
 */
export class DocumentDetailDto extends DocumentItemDto {
  content: string | null;
  filePath: string | null;
  chunkCount: number;
}

/**
 * Knowledge Pipeline 통계 DTO
 */
export class KnowledgeStatsDto {
  totalDocuments: number;
  pendingDocuments: number;
  completedDocuments: number;
  failedDocuments: number;
  totalChunks: number;
  avgChunksPerDocument: number;
  documentsByStatus: { status: string; count: number }[];
  documentsBySource: { source: string; count: number }[];
}

/**
 * 재인덱싱 요청 응답 DTO
 */
export class ReindexResponseDto {
  jobId: string;
  documentId: string;
  status: string;
  message: string;
}