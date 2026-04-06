export type UserRole = 'VIEWER' | 'EDITOR' | 'REVIEWER' | 'APPROVER' | 'ADMIN';

export interface CurrentUser {
  id: string;
  email: string;
  displayName: string;
  role: UserRole;
  securityLevelMax: string;
}

export interface TokenResponse {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
}
