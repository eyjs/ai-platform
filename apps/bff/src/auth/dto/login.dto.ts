import { IsEmail, IsNotEmpty, IsString, MinLength } from 'class-validator';

export class LoginDto {
  @IsEmail({}, { message: '유효한 이메일을 입력하세요' })
  @IsNotEmpty({ message: '이메일은 필수입니다' })
  email: string;

  @IsString()
  @IsNotEmpty({ message: '비밀번호는 필수입니다' })
  @MinLength(6, { message: '비밀번호는 6자 이상이어야 합니��' })
  password: string;
}

export class RefreshDto {
  @IsString()
  @IsNotEmpty({ message: 'refreshToken은 필��입니다' })
  refreshToken: string;
}
