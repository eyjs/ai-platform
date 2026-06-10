import {
  Injectable,
  UnauthorizedException,
  ConflictException,
} from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import * as bcrypt from 'bcrypt';
import { WebUser } from '../entities/web-user.entity';
import { jwtConfig } from '../config/jwt.config';
import { LoginDto } from './dto/login.dto';
import { TokenResponseDto, CurrentUserDto } from './dto/token-response.dto';

@Injectable()
export class AuthService {
  constructor(
    @InjectRepository(WebUser)
    private readonly userRepository: Repository<WebUser>,
    private readonly jwtService: JwtService,
  ) {}

  async login(dto: LoginDto): Promise<TokenResponseDto> {
    const user = await this.userRepository.findOne({
      where: { email: dto.email },
    });
    if (!user) {
      throw new UnauthorizedException('이메일 또는 비밀번호가 올바르지 않습니다');
    }
    if (!user.isActive) {
      throw new UnauthorizedException('비활성화된 계정입니다');
    }
    const isPasswordValid = await bcrypt.compare(dto.password, user.passwordHash);
    if (!isPasswordValid) {
      throw new UnauthorizedException('이메일 또는 비밀번호가 올바르지 않습니다');
    }
    return this.generateTokens(user);
  }

  async refresh(refreshToken: string): Promise<TokenResponseDto> {
    try {
      const payload = this.verifyRefreshToken(refreshToken);
      const user = await this.userRepository.findOne({
        where: { id: payload.sub },
      });
      if (!user || !user.isActive) {
        throw new UnauthorizedException('유효하지 않은 토큰입니다');
      }
      return this.generateTokens(user);
    } catch {
      throw new UnauthorizedException('유효하지 않은 리프레시 토큰입니다');
    }
  }

  /**
   * 리프레시 토큰 검증 (D17 듀얼-모드).
   * 토큰 헤더 alg 기준으로 키를 고정 — RS256=공개키, HS256=과도기 폴백 시 시크릿.
   */
  private verifyRefreshToken(token: string): { sub: string } {
    const headerJson = Buffer.from(token.split('.')[0] ?? '', 'base64url').toString('utf8');
    const { alg } = JSON.parse(headerJson) as { alg?: string };

    if (alg === 'RS256' && jwtConfig.publicKey) {
      return this.jwtService.verify(token, {
        publicKey: jwtConfig.publicKey,
        algorithms: ['RS256'],
      });
    }
    if (alg === 'HS256' && jwtConfig.hs256Fallback) {
      return this.jwtService.verify(token, {
        secret: jwtConfig.secret,
        algorithms: ['HS256'],
      });
    }
    throw new UnauthorizedException(`지원하지 않는 토큰 알고리즘: ${alg ?? '없음'}`);
  }

  async getMe(userId: string): Promise<CurrentUserDto> {
    const user = await this.userRepository.findOne({
      where: { id: userId },
    });
    if (!user) {
      throw new UnauthorizedException('사용자를 찾을 수 없습니다');
    }
    return {
      id: user.id,
      email: user.email,
      displayName: user.displayName,
      role: user.role,
      securityLevelMax: user.securityLevelMax,
    };
  }

  async seedAdmin(): Promise<void> {
    const existing = await this.userRepository.findOne({
      where: { email: 'admin@ai-platform.local' },
    });
    if (existing) return;

    const passwordHash = await bcrypt.hash('admin1234', 10);
    const admin = this.userRepository.create({
      email: 'admin@ai-platform.local',
      passwordHash,
      displayName: 'Administrator',
      role: 'ADMIN' as WebUser['role'],
      securityLevelMax: 'SECRET',
      isActive: true,
    });
    try {
      await this.userRepository.save(admin);
    } catch (error: unknown) {
      if (
        error instanceof Error &&
        'code' in error &&
        (error as { code: string }).code === '23505'
      ) {
        return;
      }
      throw error;
    }
  }

  private generateTokens(user: WebUser): TokenResponseDto {
    // JWT payload: FastAPI 호환 (sub, role, security_level_max — snake_case)
    const payload = {
      sub: user.id,
      email: user.email,
      role: user.role,
      security_level_max: user.securityLevelMax,
      user_type: 'web',
    };

    const accessToken = this.jwtService.sign(payload, {
      expiresIn: jwtConfig.accessExpiresIn,
    });

    const refreshToken = this.jwtService.sign(
      { sub: user.id, type: 'refresh' },
      { expiresIn: jwtConfig.refreshExpiresIn },
    );

    return {
      accessToken,
      refreshToken,
      expiresIn: jwtConfig.accessExpiresIn,
    };
  }
}
