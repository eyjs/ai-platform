export const jwtConfig = {
  secret: process.env.JWT_SECRET || 'dev-jwt-secret',
  accessExpiresIn: Number(process.env.JWT_EXPIRATION) || 900,
  refreshExpiresIn: Number(process.env.JWT_REFRESH_EXPIRATION) || 604800,
  algorithm: 'HS256' as const,
};
