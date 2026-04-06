import {
  Controller,
  Get,
  Post,
  Put,
  Delete,
  Patch,
  Param,
  Body,
  UseGuards,
  Request,
} from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { RolesGuard } from '../auth/roles.guard';
import { Roles } from '../auth/roles.decorator';
import { UserRole } from '../entities/web-user.entity';
import { ProfilesService } from './profiles.service';
import { CreateProfileDto } from './dto/create-profile.dto';
import { UpdateProfileDto, RestoreProfileDto } from './dto/update-profile.dto';

@Controller('profiles')
@UseGuards(JwtAuthGuard, RolesGuard)
@Roles(UserRole.ADMIN)
export class ProfilesController {
  constructor(private readonly profilesService: ProfilesService) {}

  @Get()
  findAll() {
    return this.profilesService.findAll();
  }

  @Get(':id')
  findOne(@Param('id') id: string) {
    return this.profilesService.findOne(id);
  }

  @Post()
  create(
    @Body() dto: CreateProfileDto,
    @Request() req: { user: { email: string } },
  ) {
    return this.profilesService.create(dto.yamlContent, req.user.email);
  }

  @Put(':id')
  update(
    @Param('id') id: string,
    @Body() dto: UpdateProfileDto,
    @Request() req: { user: { email: string } },
  ) {
    return this.profilesService.update(id, dto.yamlContent, req.user.email);
  }

  @Delete(':id')
  async remove(@Param('id') id: string) {
    await this.profilesService.remove(id);
    return { success: true };
  }

  @Patch(':id/activate')
  activate(@Param('id') id: string) {
    return this.profilesService.activate(id);
  }

  @Patch(':id/deactivate')
  deactivate(@Param('id') id: string) {
    return this.profilesService.deactivate(id);
  }

  @Get(':id/history')
  getHistory(@Param('id') id: string) {
    return this.profilesService.getHistory(id);
  }

  @Post(':id/restore')
  restore(
    @Param('id') id: string,
    @Body() dto: RestoreProfileDto,
    @Request() req: { user: { email: string } },
  ) {
    return this.profilesService.restore(id, dto.historyId, req.user.email);
  }
}

@Controller('tools')
@UseGuards(JwtAuthGuard, RolesGuard)
@Roles(UserRole.ADMIN)
export class ToolsController {
  constructor(private readonly profilesService: ProfilesService) {}

  @Get()
  getTools() {
    return this.profilesService.getTools();
  }
}
