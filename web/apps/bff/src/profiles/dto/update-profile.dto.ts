import { IsNotEmpty, IsString } from 'class-validator';

export class UpdateProfileDto {
  @IsString()
  @IsNotEmpty({ message: 'YAML 콘텐츠는 필수입니다' })
  yamlContent: string;
}

export class RestoreProfileDto {
  @IsString()
  @IsNotEmpty({ message: 'historyId는 필수입니다' })
  historyId: string;
}
