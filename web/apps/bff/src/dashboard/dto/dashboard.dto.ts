import { IsOptional, IsIn } from 'class-validator';

export class PeriodQueryDto {
  @IsOptional()
  @IsIn(['today', '7d', '30d'])
  period?: 'today' | '7d' | '30d' = 'today';
}

export class LogsQueryDto {
  @IsOptional()
  page?: number = 1;

  @IsOptional()
  size?: number = 10;

  @IsOptional()
  sort?: string = 'timestamp:desc';
}

export class DashboardSummaryDto {
  activeSessions: number;
  todayConversations: number;
  avgResponseTimeMs: number;
  errorRate: number;
  changes: {
    activeSessions: number;
    todayConversations: number;
    avgResponseTime: number;
    errorRate: number;
  };
}

export class UsageItemDto {
  profileId: string;
  profileName: string;
  count: number;
}

export class DashboardUsageDto {
  period: string;
  data: UsageItemDto[];
}

export class LatencyItemDto {
  timestamp: string;
  p50: number;
  p95: number;
}

export class DashboardLatencyDto {
  period: string;
  data: LatencyItemDto[];
}

export class LogItemDto {
  sessionId: string;
  profileId: string;
  profileName: string;
  questionPreview: string;
  responseTimeMs: number;
  timestamp: string;
}

export class DashboardLogsDto {
  data: LogItemDto[];
  total: number;
  page: number;
  size: number;
}
