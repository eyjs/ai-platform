/**
 * DocForge 파싱 결과 응답 DTO
 */
export class ParseResultDto {
  markdown: string;
  metadata: Record<string, unknown>;
  stats: Record<string, unknown>;
}

/**
 * DocForge 상태 확인 응답 DTO
 */
export class ParseHealthDto {
  status: string;
  version: string;
}

/**
 * 성공 응답 래퍼
 */
export class ParseSuccessResponseDto {
  success: true;
  data: ParseResultDto;
}

export class ParseHealthResponseDto {
  success: true;
  data: ParseHealthDto;
}
