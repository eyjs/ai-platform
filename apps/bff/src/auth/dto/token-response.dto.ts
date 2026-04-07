export class TokenResponseDto {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}

export class CurrentUserDto {
  id: string;
  email: string;
  displayName: string;
  role: string;
  securityLevelMax: string;
}
