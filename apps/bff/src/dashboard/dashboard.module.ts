import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { AgentProfile } from '../entities/agent-profile.entity';
import { DashboardController } from './dashboard.controller';
import { DashboardService } from './dashboard.service';

@Module({
  imports: [TypeOrmModule.forFeature([AgentProfile])],
  controllers: [DashboardController],
  providers: [DashboardService],
})
export class DashboardModule {}
