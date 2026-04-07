import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { AgentProfile } from '../entities/agent-profile.entity';
import { ProfileHistory } from '../entities/profile-history.entity';
import { ProfilesController, ToolsController } from './profiles.controller';
import { ProfilesService } from './profiles.service';

@Module({
  imports: [TypeOrmModule.forFeature([AgentProfile, ProfileHistory])],
  controllers: [ProfilesController, ToolsController],
  providers: [ProfilesService],
  exports: [ProfilesService],
})
export class ProfilesModule {}
