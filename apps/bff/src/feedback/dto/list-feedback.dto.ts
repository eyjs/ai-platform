import { Transform, Type } from 'class-transformer';
import {
  IsBoolean,
  IsInt,
  IsISO8601,
  IsOptional,
  Max,
  Min,
} from 'class-validator';

function toBool(value: unknown): boolean {
  return value === true || value === 'true' || value === 1 || value === '1';
}

/**
 * GET /bff/admin/feedback query.
 * Contract: .pipeline/contracts/feedback-dto.md
 */
export class ListFeedbackDto {
  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(200)
  limit?: number;

  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(0)
  offset?: number;

  @IsOptional()
  @Transform(({ value }) => toBool(value))
  @IsBoolean()
  only_negative?: boolean;

  @IsOptional()
  @IsISO8601()
  date_from?: string;

  @IsOptional()
  @IsISO8601()
  date_to?: string;
}

export interface AdminFeedbackItem {
  id: string;
  response_id: string;
  score: number;
  comment: string | null;
  created_at: string;
  user_id: string;
  profile_id: string | null;
  faithfulness_score: number | null;
  question_preview: string | null;
  answer_preview: string | null;
  response_ts: string | null;
}

export interface AdminFeedbackPage {
  items: AdminFeedbackItem[];
  total: number;
  limit: number;
  offset: number;
}
