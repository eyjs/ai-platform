import { UnauthorizedException } from '@nestjs/common';
import { AuthService } from './auth.service';
import { WebUser, UserRole } from '../entities/web-user.entity';
import { jwtConfig } from '../config/jwt.config';

jest.mock('bcrypt');
import * as bcrypt from 'bcrypt';

const mockedBcrypt = bcrypt as jest.Mocked<typeof bcrypt>;

function makeWebUser(overrides: Partial<WebUser> = {}): WebUser {
  const user = new WebUser();
  user.id = 'user-uuid-1234';
  user.email = 'test@example.com';
  user.passwordHash = '$2b$10$hashedpassword';
  user.displayName = 'Test User';
  user.role = UserRole.VIEWER;
  user.securityLevelMax = 'PUBLIC';
  user.isActive = true;
  user.createdAt = new Date('2024-01-01');
  user.updatedAt = new Date('2024-01-01');
  return Object.assign(user, overrides);
}

function makeUserRepo(overrides: Partial<{ findOne: jest.Mock; create: jest.Mock; save: jest.Mock }> = {}) {
  return {
    findOne: jest.fn(),
    create: jest.fn(),
    save: jest.fn(),
    ...overrides,
  };
}

function makeJwtService(overrides: Partial<{ sign: jest.Mock; verify: jest.Mock }> = {}) {
  return {
    sign: jest.fn().mockReturnValue('mock-token'),
    verify: jest.fn(),
    ...overrides,
  };
}

describe('AuthService', () => {
  let userRepo: ReturnType<typeof makeUserRepo>;
  let jwtService: ReturnType<typeof makeJwtService>;
  let authService: AuthService;

  beforeEach(() => {
    userRepo = makeUserRepo();
    jwtService = makeJwtService();
    authService = new AuthService(userRepo as never, jwtService as never);
    jest.clearAllMocks();
    jwtService.sign.mockReturnValue('mock-token');
  });

  describe('login', () => {
    it('유효한 자격증명으로 로그인하면 tokens를 반환한다', async () => {
      // Arrange
      const user = makeWebUser();
      userRepo.findOne.mockResolvedValue(user);
      mockedBcrypt.compare.mockResolvedValue(true as never);

      // Act
      const result = await authService.login({ email: 'test@example.com', password: 'password123' });

      // Assert
      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
      expect(result).toHaveProperty('expiresIn');
      expect(result.expiresIn).toBe(jwtConfig.accessExpiresIn);
    });

    it('존재하지 않는 유저이면 UnauthorizedException을 던진다', async () => {
      // Arrange
      userRepo.findOne.mockResolvedValue(null);

      // Act & Assert
      await expect(
        authService.login({ email: 'notfound@example.com', password: 'password123' }),
      ).rejects.toThrow(UnauthorizedException);
    });

    it('비활성화된 계정이면 UnauthorizedException을 던진다', async () => {
      // Arrange
      const user = makeWebUser({ isActive: false });
      userRepo.findOne.mockResolvedValue(user);

      // Act & Assert
      await expect(
        authService.login({ email: 'test@example.com', password: 'password123' }),
      ).rejects.toThrow(UnauthorizedException);
    });

    it('비밀번호가 일치하지 않으면 UnauthorizedException을 던진다', async () => {
      // Arrange
      const user = makeWebUser();
      userRepo.findOne.mockResolvedValue(user);
      mockedBcrypt.compare.mockResolvedValue(false as never);

      // Act & Assert
      await expect(
        authService.login({ email: 'test@example.com', password: 'wrongpassword' }),
      ).rejects.toThrow(UnauthorizedException);
    });
  });

  describe('refresh', () => {
    it('유효한 refresh 토큰으로 새 tokens를 반환한다', async () => {
      // Arrange
      const user = makeWebUser();
      jwtService.verify.mockReturnValue({ sub: user.id });
      userRepo.findOne.mockResolvedValue(user);

      // Act
      const result = await authService.refresh('valid-refresh-token');

      // Assert
      expect(result).toHaveProperty('accessToken');
      expect(result).toHaveProperty('refreshToken');
      expect(jwtService.verify).toHaveBeenCalledWith('valid-refresh-token', {
        secret: jwtConfig.secret,
      });
    });

    it('유효하지 않은 refresh 토큰이면 UnauthorizedException을 던진다', async () => {
      // Arrange
      jwtService.verify.mockImplementation(() => {
        throw new Error('invalid token');
      });

      // Act & Assert
      await expect(authService.refresh('invalid-token')).rejects.toThrow(UnauthorizedException);
    });

    it('토큰의 sub에 해당하는 유저가 없으면 UnauthorizedException을 던진다', async () => {
      // Arrange
      jwtService.verify.mockReturnValue({ sub: 'nonexistent-user-id' });
      userRepo.findOne.mockResolvedValue(null);

      // Act & Assert
      await expect(authService.refresh('valid-refresh-token')).rejects.toThrow(UnauthorizedException);
    });

    it('비활성화된 유저의 refresh 토큰이면 UnauthorizedException을 던진다', async () => {
      // Arrange
      const user = makeWebUser({ isActive: false });
      jwtService.verify.mockReturnValue({ sub: user.id });
      userRepo.findOne.mockResolvedValue(user);

      // Act & Assert
      await expect(authService.refresh('valid-token')).rejects.toThrow(UnauthorizedException);
    });
  });

  describe('getMe', () => {
    it('유효한 userId로 CurrentUserDto를 반환한다', async () => {
      // Arrange
      const user = makeWebUser();
      userRepo.findOne.mockResolvedValue(user);

      // Act
      const result = await authService.getMe(user.id);

      // Assert
      expect(result).toEqual({
        id: user.id,
        email: user.email,
        displayName: user.displayName,
        role: user.role,
        securityLevelMax: user.securityLevelMax,
      });
    });

    it('존재하지 않는 userId이면 UnauthorizedException을 던진다', async () => {
      // Arrange
      userRepo.findOne.mockResolvedValue(null);

      // Act & Assert
      await expect(authService.getMe('nonexistent-id')).rejects.toThrow(UnauthorizedException);
    });
  });

  describe('generateTokens (토큰 payload 구조 검증)', () => {
    it('accessToken에는 sub, email, role, security_level_max, user_type이 포함된 payload를 사용한다', async () => {
      // Arrange
      const user = makeWebUser({ role: UserRole.ADMIN, securityLevelMax: 'SECRET' });
      userRepo.findOne.mockResolvedValue(user);
      mockedBcrypt.compare.mockResolvedValue(true as never);

      // Act
      await authService.login({ email: user.email, password: 'password' });

      // Assert — 첫 번째 sign 호출이 accessToken (payload 검증)
      expect(jwtService.sign).toHaveBeenCalledWith(
        expect.objectContaining({
          sub: user.id,
          email: user.email,
          role: user.role,
          security_level_max: user.securityLevelMax,
          user_type: 'web',
        }),
        expect.objectContaining({ expiresIn: jwtConfig.accessExpiresIn }),
      );
    });

    it('refreshToken에는 sub와 type:refresh 만 포함된 payload를 사용한다', async () => {
      // Arrange
      const user = makeWebUser();
      userRepo.findOne.mockResolvedValue(user);
      mockedBcrypt.compare.mockResolvedValue(true as never);

      // Act
      await authService.login({ email: user.email, password: 'password' });

      // Assert — 두 번째 sign 호출이 refreshToken
      expect(jwtService.sign).toHaveBeenNthCalledWith(
        2,
        { sub: user.id, type: 'refresh' },
        expect.objectContaining({ expiresIn: jwtConfig.refreshExpiresIn }),
      );
    });
  });
});
