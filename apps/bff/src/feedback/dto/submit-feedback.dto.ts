import {
  IsIn,
  IsOptional,
  IsString,
  IsUUID,
  MaxLength,
} from 'class-validator';

/**
 * POST /bff/feedback body.
 * Contract: .pipeline/contracts/feedback-dto.md
 */
export class SubmitFeedbackDto {
  @IsUUID()
  response_id!: string;

  @IsIn([1, -1])
  score!: 1 | -1;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  comment?: string;
}
