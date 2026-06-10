import { Module, OnModuleInit } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { PassportModule } from '@nestjs/passport';
import { TypeOrmModule } from '@nestjs/typeorm';
import { jwtConfig } from '../config/jwt.config';
import { WebUser } from '../entities/web-user.entity';
import { AuthController } from './auth.controller';
import { AuthService } from './auth.service';
import { JwtStrategy } from './jwt.strategy';

@Module({
  imports: [
    TypeOrmModule.forFeature([WebUser]),
    PassportModule.register({ defaultStrategy: 'jwt' }),
    // D17: 개인키가 있으면 RS256 서명(+kid), 없으면 레거시 HS256
    JwtModule.register(
      jwtConfig.privateKey
        ? {
            privateKey: jwtConfig.privateKey,
            publicKey: jwtConfig.publicKey,
            signOptions: { algorithm: 'RS256', keyid: jwtConfig.kid },
          }
        : {
            secret: jwtConfig.secret,
            signOptions: { algorithm: 'HS256' },
          },
    ),
  ],
  controllers: [AuthController],
  providers: [AuthService, JwtStrategy],
  exports: [AuthService, JwtModule],
})
export class AuthModule implements OnModuleInit {
  constructor(private readonly authService: AuthService) {}

  async onModuleInit() {
    await this.authService.seedAdmin();
  }
}
