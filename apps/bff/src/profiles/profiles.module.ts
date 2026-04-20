import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { AgentProfile } from '../entities/agent-profile.entity';
import { ProfileHistory } from '../entities/profile-history.entity';
import { ProfilesController, ToolsController } from './profiles.controller';
import { ProfilesService } from './profiles.service';
import { ProfileSchemaValidator } from './profile-schema.validator';

@Module({
  imports: [TypeOrmModule.forFeature([AgentProfile, ProfileHistory])],
  controllers: [ProfilesController, ToolsController],
  providers: [ProfilesService, ProfileSchemaValidator],
  exports: [ProfilesService, ProfileSchemaValidator],
})
export class ProfilesModule {}
