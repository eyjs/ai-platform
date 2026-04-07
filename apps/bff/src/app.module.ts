import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { getDatabaseConfig } from './config/database.config';
import { AuthModule } from './auth/auth.module';
import { ProfilesModule } from './profiles/profiles.module';
import { DashboardModule } from './dashboard/dashboard.module';
import { AppController } from './app.controller';

@Module({
  imports: [
    TypeOrmModule.forRoot(getDatabaseConfig()),
    AuthModule,
    ProfilesModule,
    DashboardModule,
  ],
  controllers: [AppController],
  providers: [],
})
export class AppModule {}
