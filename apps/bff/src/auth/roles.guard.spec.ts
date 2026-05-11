import { ForbiddenException, ExecutionContext } from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import { RolesGuard } from './roles.guard';
import { UserRole } from '../entities/web-user.entity';
import { ROLES_KEY } from './roles.decorator';

function makeContext(user: { role: UserRole } | null | undefined): ExecutionContext {
  const request = user !== undefined ? { user } : {};
  return {
    getHandler: jest.fn().mockReturnValue({}),
    getClass: jest.fn().mockReturnValue({}),
    switchToHttp: jest.fn().mockReturnValue({
      getRequest: jest.fn().mockReturnValue(request),
    }),
  } as unknown as ExecutionContext;
}

function makeReflector(roles: UserRole[] | undefined): Reflector {
  return {
    getAllAndOverride: jest.fn().mockReturnValue(roles),
  } as unknown as Reflector;
}

describe('RolesGuard', () => {
  describe('역할 메타데이터 없음', () => {
    it('requiredRoles가 undefined이면 true를 반환한다', () => {
      // Arrange
      const reflector = makeReflector(undefined);
      const guard = new RolesGuard(reflector);
      const context = makeContext({ role: UserRole.VIEWER });

      // Act
      const result = guard.canActivate(context);

      // Assert
      expect(result).toBe(true);
    });

    it('requiredRoles가 빈 배열이면 true를 반환한다', () => {
      // Arrange
      const reflector = makeReflector([]);
      const guard = new RolesGuard(reflector);
      const context = makeContext({ role: UserRole.VIEWER });

      // Act
      const result = guard.canActivate(context);

      // Assert
      expect(result).toBe(true);
    });

    it('Reflector가 ROLES_KEY로 호출되었는지 확인한다', () => {
      // Arrange
      const reflector = makeReflector(undefined);
      const guard = new RolesGuard(reflector);
      const context = makeContext({ role: UserRole.VIEWER });
      const handler = context.getHandler();
      const cls = context.getClass();

      // Act
      guard.canActivate(context);

      // Assert
      expect(reflector.getAllAndOverride).toHaveBeenCalledWith(ROLES_KEY, [handler, cls]);
    });
  });

  describe('올바른 역할', () => {
    it('유저의 역할이 required 역할에 포함되면 true를 반환한다', () => {
      // Arrange
      const reflector = makeReflector([UserRole.ADMIN]);
      const guard = new RolesGuard(reflector);
      const context = makeContext({ role: UserRole.ADMIN });

      // Act
      const result = guard.canActivate(context);

      // Assert
      expect(result).toBe(true);
    });

    it('여러 required 역할 중 하나에 매치되면 true를 반환한다', () => {
      // Arrange
      const reflector = makeReflector([UserRole.ADMIN, UserRole.EDITOR]);
      const guard = new RolesGuard(reflector);
      const context = makeContext({ role: UserRole.EDITOR });

      // Act
      const result = guard.canActivate(context);

      // Assert
      expect(result).toBe(true);
    });
  });

  describe('잘못된 역할', () => {
    it('유저의 역할이 required 역할에 포함되지 않으면 ForbiddenException을 던진다', () => {
      // Arrange
      const reflector = makeReflector([UserRole.ADMIN]);
      const guard = new RolesGuard(reflector);
      const context = makeContext({ role: UserRole.VIEWER });

      // Act & Assert
      expect(() => guard.canActivate(context)).toThrow(ForbiddenException);
    });

    it('VIEWER가 ADMIN 전용 리소스에 접근하면 ForbiddenException을 던진다', () => {
      // Arrange
      const reflector = makeReflector([UserRole.ADMIN]);
      const guard = new RolesGuard(reflector);
      const context = makeContext({ role: UserRole.VIEWER });

      // Act & Assert
      expect(() => guard.canActivate(context)).toThrow('접근 권한이 없습니다');
    });
  });

  describe('user 없음', () => {
    it('request에 user가 없으면 ForbiddenException을 던진다', () => {
      // Arrange
      const reflector = makeReflector([UserRole.ADMIN]);
      const guard = new RolesGuard(reflector);
      const context = makeContext(null);

      // Act & Assert
      expect(() => guard.canActivate(context)).toThrow(ForbiddenException);
    });

    it('request에 user가 없을 때 에러 메시지가 인증 필요임을 알린다', () => {
      // Arrange
      const reflector = makeReflector([UserRole.ADMIN]);
      const guard = new RolesGuard(reflector);
      const context = makeContext(null);

      // Act & Assert
      expect(() => guard.canActivate(context)).toThrow('인증이 필요합니다');
    });
  });
});
