import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  // CORS — 콤마 구분 환경변수 또는 기본 허용 목록
  const corsOrigins = (
    process.env.CORS_ORIGIN ||
    'http://localhost:3000,https://ai-platform-eight-sigma.vercel.app'
  )
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);

  app.enableCors({
    origin: corsOrigins.includes('*') ? true : corsOrigins,
    credentials: true,
  });

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      forbidNonWhitelisted: true,
      transform: true,
    }),
  );

  app.setGlobalPrefix('bff');

  const port = process.env.BFF_PORT || 3001;
  await app.listen(port);
  console.log(`BFF server running on http://localhost:${port}`);
}

bootstrap();
