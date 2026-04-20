import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { AgentProfile } from '../entities/agent-profile.entity';
import { ApiRequestLog } from '../entities/api-request-log.entity';
import { DashboardController } from './dashboard.controller';
import { DashboardService } from './dashboard.service';

@Module({
  imports: [TypeOrmModule.forFeature([AgentProfile, ApiRequestLog])],
  controllers: [DashboardController],
  providers: [DashboardService],
})
export class DashboardModule {}
