import {
  Body,
  Controller,
  Get,
  Param,
  Patch,
  Post,
  Query,
  Req,
  UseGuards,
} from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { RolesGuard } from '../auth/roles.guard';
import { Roles } from '../auth/roles.decorator';
import { UserRole } from '../entities/web-user.entity';
import { ApiKeysService } from './api-keys.service';
import { CreateApiKeyDto } from './dto/create-api-key.dto';
import { UpdateApiKeyDto } from './dto/update-api-key.dto';

interface RequestWithUser extends Request {
  user?: { sub?: string; email?: string };
}

function getActor(req: RequestWithUser): string {
  return req.user?.sub ?? req.user?.email ?? 'unknown';
}

@Controller('api-keys')
@UseGuards(JwtAuthGuard, RolesGuard)
@Roles(UserRole.ADMIN)
export class ApiKeysController {
  constructor(private readonly service: ApiKeysService) {}

  @Get()
  list() {
    return this.service.list();
  }

  @Get(':id')
  findOne(@Param('id') id: string) {
    return this.service.findOne(id);
  }

  @Post()
  create(@Body() dto: CreateApiKeyDto, @Req() req: RequestWithUser) {
    return this.service.create(dto, getActor(req));
  }

  @Patch(':id')
  update(
    @Param('id') id: string,
    @Body() dto: UpdateApiKeyDto,
    @Req() req: RequestWithUser,
  ) {
    return this.service.update(id, dto, getActor(req));
  }

  @Post(':id/revoke')
  revoke(@Param('id') id: string, @Req() req: RequestWithUser) {
    return this.service.revoke(id, getActor(req));
  }

  @Post(':id/rotate')
  rotate(@Param('id') id: string, @Req() req: RequestWithUser) {
    return this.service.rotate(id, getActor(req));
  }

  @Get(':id/audit')
  audit(@Param('id') id: string, @Query('limit') limit?: string) {
    const n = limit ? parseInt(limit, 10) : 50;
    return this.service.getAudit(id, Number.isFinite(n) ? n : 50);
  }
}
