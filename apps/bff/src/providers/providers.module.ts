import { Module } from '@nestjs/common';
import { TypeOrmModule } from '@nestjs/typeorm';
import { CacheEntry } from '../entities/cache-entry.entity';
import { ProvidersController } from './providers.controller';
import { ProvidersService } from './providers.service';

@Module({
  imports: [TypeOrmModule.forFeature([CacheEntry])],
  controllers: [ProvidersController],
  providers: [ProvidersService],
})
export class ProvidersModule {}