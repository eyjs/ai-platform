import { TypeOrmModuleOptions } from '@nestjs/typeorm';

export function getDatabaseConfig(): TypeOrmModuleOptions {
  return {
    type: 'postgres',
    url:
      process.env.DATABASE_URL ||
      'postgresql://postgres:postgres@localhost:5432/ai_platform',
    entities: [__dirname + '/../entities/*.entity{.ts,.js}'],
    migrations: [__dirname + '/../migrations/*{.ts,.js}'],
    synchronize: false,
    logging: process.env.NODE_ENV !== 'production',
  };
}
