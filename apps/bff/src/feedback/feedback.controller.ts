import {
  Body,
  Controller,
  Get,
  Post,
  Query,
  Req,
  UseGuards,
} from '@nestjs/common';
import type { Request } from 'express';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { RolesGuard } from '../auth/roles.guard';
import { Roles } from '../auth/roles.decorator';
import { UserRole } from '../entities/web-user.entity';
import { FeedbackService } from './feedback.service';
import { SubmitFeedbackDto } from './dto/submit-feedback.dto';
import { ListFeedbackDto } from './dto/list-feedback.dto';

/**
 * Feedback Controller.
 * - POST /bff/feedback  → api 중계 (JWT 필요)
 * - GET  /bff/admin/feedback → DB 직접 조회 (ADMIN)
 */
@Controller()
export class FeedbackController {
  constructor(private readonly feedbackService: FeedbackService) {}

  @Post('feedback')
  @UseGuards(JwtAuthGuard)
  async submit(@Body() dto: SubmitFeedbackDto, @Req() req: Request) {
    const authorization = req.headers['authorization'];
    return this.feedbackService.submit(dto, authorization);
  }

  @Get('admin/feedback')
  @UseGuards(JwtAuthGuard, RolesGuard)
  @Roles(UserRole.ADMIN)
  async list(@Query() query: ListFeedbackDto) {
    return this.feedbackService.list(query);
  }
}
