import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { getDatabaseConfig } from './config/database.config';
import { AuthModule } from './auth/auth.module';
import { ProfilesModule } from './profiles/profiles.module';
import { DashboardModule } from './dashboard/dashboard.module';
import { ApiKeysModule } from './api-keys/api-keys.module';
import { FeedbackModule } from './feedback/feedback.module';
import { RequestLogsModule } from './request-logs/request-logs.module';
import { KnowledgeModule } from './knowledge/knowledge.module';
import { ProvidersModule } from './providers/providers.module';
import { AppController } from './app.controller';

@Module({
  imports: [
    TypeOrmModule.forRoot(getDatabaseConfig()),
    AuthModule,
    ProfilesModule,
    DashboardModule,
    ApiKeysModule,
    FeedbackModule,
    RequestLogsModule,
    KnowledgeModule,
    ProvidersModule,
  ],
  controllers: [AppController],
  providers: [],
})
export class AppModule {}
