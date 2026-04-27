import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { ApiRequestLog } from '../entities/api-request-log.entity';
import { RequestLogsController } from './request-logs.controller';
import { RequestLogsService } from './request-logs.service';

@Module({
  imports: [TypeOrmModule.forFeature([ApiRequestLog])],
  controllers: [RequestLogsController],
  providers: [RequestLogsService],
})
export class RequestLogsModule {}