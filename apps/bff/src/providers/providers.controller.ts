import { Controller, Get, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { ProvidersService } from './providers.service';
import { ProvidersStatusDto } from './dto/provider-status.dto';

/**
 * Providers 컨트롤러
 * cache_entries 테이블에서 Provider 상태 조회 API
 */
@Controller('providers')
@UseGuards(JwtAuthGuard)
export class ProvidersController {
  constructor(private readonly providersService: ProvidersService) {}

  /**
   * Provider 상태 조회
   * GET /bff/providers/status
   */
  @Get('status')
  async getStatus(): Promise<ProvidersStatusDto> {
    return this.providersService.getProvidersStatus();
  }
}