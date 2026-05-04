/** PDF 파싱 결과 데이터 */
export interface ParseResultData {
  markdown: string;
  metadata: Record<string, unknown>;
  stats: Record<string, unknown>;
}

/** BFF /bff/parse/upload 성공 응답 */
export interface ParseSuccessResponse {
  success: true;
  data: ParseResultData;
}

/** BFF 에러 응답 */
export interface ParseErrorResponse {
  success: false;
  error: {
    code: string;
    message: string;
  };
}

/** BFF /bff/parse/health 성공 응답 */
export interface ParseHealthResponse {
  success: true;
  data: {
    status: string;
    version: string;
  };
}

/** 파싱 페이지 상태 */
export type ParseStatus = 'idle' | 'uploading' | 'parsing' | 'done' | 'error';

/** 파싱 에러 정보 */
export interface ParseError {
  code: string;
  message: string;
}
