import {
  IsArray,
  IsEnum,
  IsInt,
  IsISO8601,
  IsOptional,
  IsString,
  Length,
  Max,
  Min,
} from 'class-validator';

export class CreateApiKeyDto {
  @IsString()
  @Length(1, 100)
  name!: string;

  @IsArray()
  @IsString({ each: true })
  allowed_profiles!: string[];

  @IsInt()
  @Min(1)
  @Max(10000)
  rate_limit_per_min!: number;

  @IsInt()
  @Min(1)
  @Max(10_000_000)
  rate_limit_per_day!: number;

  @IsEnum(['PUBLIC', 'INTERNAL', 'CONFIDENTIAL'])
  security_level_max!: 'PUBLIC' | 'INTERNAL' | 'CONFIDENTIAL';

  @IsOptional()
  @IsISO8601()
  expires_at?: string | null;
}
