import {
  Controller,
  Post,
  Get,
  UseGuards,
  UseInterceptors,
  UploadedFile,
  BadRequestException,
  UnsupportedMediaTypeException,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { ParseService, UploadedFile as ParseUploadedFile, ParseResult, HealthResult } from './parse.service';

const ALLOWED_MIMES = new Set(['application/pdf']);
const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100MB

/**
 * Parse 컨트롤러
 * DocForge 프록시 — JWT 인증 후 X-Internal-Key를 주입하여 DocForge에 중계
 */
@Controller('parse')
@UseGuards(JwtAuthGuard)
export class ParseController {
  constructor(private readonly parseService: ParseService) {}

  /**
   * PDF 업로드 + 동기 파싱
   * POST /bff/parse/upload
   */
  @Post('upload')
  @UseInterceptors(FileInterceptor('file'))
  async upload(
    @UploadedFile() file: ParseUploadedFile | undefined,
  ): Promise<{ success: true; data: ParseResult }> {
    if (!file || !file.buffer || !file.originalname) {
      throw new BadRequestException({
        success: false,
        error: {
          code: 'NO_FILE',
          message: "파일이 필요합니다. 'file' 필드로 PDF를 전송하세요.",
        },
      });
    }

    if (file.size > MAX_FILE_SIZE) {
      throw new BadRequestException({
        success: false,
        error: {
          code: 'FILE_TOO_LARGE',
          message: `파일 크기가 ${MAX_FILE_SIZE / 1024 / 1024}MB를 초과합니다.`,
        },
      });
    }

    const baseMime = (file.mimetype || '').split(';')[0].trim().toLowerCase();
    if (!ALLOWED_MIMES.has(baseMime)) {
      throw new UnsupportedMediaTypeException({
        success: false,
        error: {
          code: 'UNSUPPORTED_MEDIA_TYPE',
          message: `지원하지 않는 파일 형식입니다: ${file.mimetype}. application/pdf만 허용됩니다.`,
        },
      });
    }

    const result = await this.parseService.uploadAndParse(file);

    return {
      success: true,
      data: result,
    };
  }

  /**
   * DocForge 서버 상태 확인
   * GET /bff/parse/health
   */
  @Get('health')
  async health(): Promise<{ success: true; data: HealthResult }> {
    const result = await this.parseService.checkHealth();

    return {
      success: true,
      data: result,
    };
  }
}
