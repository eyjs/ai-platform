import { IsNotEmpty, IsString } from 'class-validator';

export class CreateProfileDto {
  @IsString()
  @IsNotEmpty({ message: 'YAML 콘텐츠는 필수입니다' })
  yamlContent: string;
}
