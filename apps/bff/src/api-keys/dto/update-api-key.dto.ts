import {
  IsArray,
  IsBoolean,
  IsEnum,
  IsInt,
  IsISO8601,
  IsOptional,
  IsString,
  Length,
  Max,
  Min,
} from 'class-validator';

export class UpdateApiKeyDto {
  @IsOptional()
  @IsString()
  @Length(1, 100)
  name?: string;

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  allowed_profiles?: string[];

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(10000)
  rate_limit_per_min?: number;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(10_000_000)
  rate_limit_per_day?: number;

  @IsOptional()
  @IsEnum(['PUBLIC', 'INTERNAL', 'CONFIDENTIAL'])
  security_level_max?: 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL';

  @IsOptional()
  @IsISO8601()
  expires_at?: string | null;

  @IsOptional()
  @IsBoolean()
  is_active?: boolean;
}
