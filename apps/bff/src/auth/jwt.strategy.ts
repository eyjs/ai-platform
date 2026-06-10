import { Injectable } from '@nestjs/common';
import { PassportStrategy } from '@nestjs/passport';
import { ExtractJwt, Strategy } from 'passport-jwt';
import { jwtConfig } from '../config/jwt.config';

interface JwtPayload {
  sub: string;
  email: string;
  role: string;
  security_level_max: string;
}

/**
 * 토큰 헤더의 alg에 따라 검증 키를 고정한다 (D17 듀얼-모드).
 * 키와 알고리즘을 교차시키지 않아 알고리즘 혼동 공격을 차단한다:
 *   RS256 → 공개키만, HS256 → (과도기 폴백 시) 공유 시크릿만.
 */
function selectVerificationKey(rawJwtToken: string): string {
  const headerJson = Buffer.from(rawJwtToken.split('.')[0] ?? '', 'base64url').toString('utf8');
  const { alg } = JSON.parse(headerJson) as { alg?: string };

  if (alg === 'RS256') {
    if (!jwtConfig.publicKey) {
      throw new Error('RS256 토큰을 검증할 공개키가 설정되지 않았습니다');
    }
    return jwtConfig.publicKey;
  }
  if (alg === 'HS256') {
    if (!jwtConfig.hs256Fallback) {
      throw new Error('HS256 토큰은 더 이상 허용되지 않습니다 (RS256 전환 완료)');
    }
    return jwtConfig.secret;
  }
  throw new Error(`지원하지 않는 JWT 알고리즘: ${alg ?? '없음'}`);
}

@Injectable()
export class JwtStrategy extends PassportStrategy(Strategy) {
  constructor() {
    super({
      jwtFromRequest: ExtractJwt.fromAuthHeaderAsBearerToken(),
      ignoreExpiration: false,
      algorithms: ['RS256', 'HS256'],
      secretOrKeyProvider: (
        _request: unknown,
        rawJwtToken: string,
        done: (err: Error | null, key?: string) => void,
      ) => {
        try {
          done(null, selectVerificationKey(rawJwtToken));
        } catch (e) {
          done(e as Error);
        }
      },
    });
  }

  validate(payload: JwtPayload) {
    return {
      id: payload.sub,
      email: payload.email,
      role: payload.role,
      securityLevelMax: payload.security_level_max,
    };
  }
}
