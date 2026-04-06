import { Controller, Get, Query, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { RolesGuard } from '../auth/roles.guard';
import { Roles } from '../auth/roles.decorator';
import { UserRole } from '../entities/web-user.entity';
import { DashboardService } from './dashboard.service';
import { PeriodQueryDto, LogsQueryDto } from './dto/dashboard.dto';

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
}
