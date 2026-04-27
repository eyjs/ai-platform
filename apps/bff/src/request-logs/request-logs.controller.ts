import { Controller, Get, Query, Param, ParseUUIDPipe, NotFoundException, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { RequestLogsService } from './request-logs.service';
import {
  QueryRequestLogsDto,
  RequestLogsResponseDto,
  RequestLogItemDto,
  RequestLogsStatsDto,
} from './dto/query-request-logs.dto';

/**
 * 요청 로그 컨트롤러
 * api_request_logs 테이블 조회 API
 */
@Controller('request-logs')
@UseGuards(JwtAuthGuard)
export class RequestLogsController {
  constructor(private readonly requestLogsService: RequestLogsService) {}

  /**
   * 요청 로그 목록 조회
   * GET /bff/request-logs?profile_id=...&status=...&date_from=...&date_to=...&page=1&size=20
   */
  @Get()
  async getLogs(@Query() query: QueryRequestLogsDto): Promise<RequestLogsResponseDto> {
    return this.requestLogsService.findLogs(query);
  }

  /**
   * 요청 통계 조회
   * GET /bff/request-logs/stats
   */
  @Get('stats')
  async getStats(): Promise<RequestLogsStatsDto> {
    return this.requestLogsService.getStats(24);
  }

  /**
   * 단건 로그 상세 조회
   * GET /bff/request-logs/:id
   */
  @Get(':id')
  async getLog(@Param('id', ParseUUIDPipe) id: string): Promise<RequestLogItemDto> {
    const log = await this.requestLogsService.findLogById(id);

    if (!log) {
      throw new NotFoundException(`Request log with id ${id} not found`);
    }

    return log;
  }
}