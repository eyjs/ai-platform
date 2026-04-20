import { Controller, Get, Param, Query, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { RolesGuard } from '../auth/roles.guard';
import { Roles } from '../auth/roles.decorator';
import { UserRole } from '../entities/web-user.entity';
import { DashboardService } from './dashboard.service';
import { PeriodQueryDto, LogsQueryDto } from './dto/dashboard.dto';
import {
  RangeQueryDto,
  RangeBucketQueryDto,
  DashboardRange,
  DashboardBucket,
} from './dto/api-key-metrics.dto';

@Controller('dashboard')
@UseGuards(JwtAuthGuard, RolesGuard)
@Roles(UserRole.ADMIN)
export class DashboardController {
  constructor(private readonly dashboardService: DashboardService) {}

  @Get('summary')
  getSummary() {
    return this.dashboardService.getSummary();
  }

  @Get('usage')
  getUsage(@Query() query: PeriodQueryDto) {
    return this.dashboardService.getUsage(query.period || 'today');
  }

  @Get('latency')
  getLatency(@Query() query: PeriodQueryDto) {
    return this.dashboardService.getLatency(query.period || 'today');
  }

  @Get('logs')
  getLogs(@Query() query: LogsQueryDto) {
    return this.dashboardService.getLogs(
      Number(query.page) || 1,
      Number(query.size) || 10,
      query.sort || 'timestamp:desc',
    );
  }

  // ---- API Key 전용 엔드포인트 (Task 007) ----

  @Get('api-keys/:id/summary')
  getKeySummary(@Param('id') id: string, @Query() q: RangeQueryDto) {
    return this.dashboardService.getKeySummary(id, (q.range || '24h') as DashboardRange);
  }

  @Get('api-keys/:id/profile-breakdown')
  getKeyProfileBreakdown(@Param('id') id: string, @Query() q: RangeQueryDto) {
    return this.dashboardService.getKeyProfileBreakdown(id, (q.range || '24h') as DashboardRange);
  }

  @Get('api-keys/:id/timeline')
  getKeyTimeline(@Param('id') id: string, @Query() q: RangeBucketQueryDto) {
    const range = (q.range || '24h') as DashboardRange;
    const bucket = (q.bucket || (range === '30d' ? 'day' : 'hour')) as DashboardBucket;
    return this.dashboardService.getKeyTimeline(id, range, bucket);
  }

  @Get('api-keys/:id/recent')
  getKeyRecent(@Param('id') id: string, @Query('limit') limit?: string) {
    const n = limit ? parseInt(limit, 10) : 100;
    return this.dashboardService.getKeyRecentLogs(id, Number.isFinite(n) ? n : 100);
  }
}
