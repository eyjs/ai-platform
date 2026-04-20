import {
  BadRequestException,
  Injectable,
  InternalServerErrorException,
  NotFoundException,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { DataSource, Repository } from 'typeorm';
import { createHash, randomBytes } from 'crypto';
import { ApiKey } from '../entities/api-key.entity';
import { ApiKeyAuditLog } from '../entities/api-key-audit-log.entity';
import { CreateApiKeyDto } from './dto/create-api-key.dto';
import { UpdateApiKeyDto } from './dto/update-api-key.dto';
import {
  ApiKeyAuditEntryDto,
  ApiKeyCreateResponseDto,
  ApiKeyResponseDto,
} from './dto/api-key-response.dto';

const AUDIT_WHITELIST: (keyof ApiKey)[] = [
  'name',
  'allowedProfiles',
  'rateLimitPerMin',
  'rateLimitPerDay',
  'securityLevelMax',
  'expiresAt',
  'isActive',
];

const BASE58_ALPHABET =
  '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz';

function encodeBase58(buf: Buffer): string {
  // simple base58 encoding (not constant-time; OK for key generation)
  let n = 0n;
  for (const b of buf) n = (n << 8n) + BigInt(b);
  let out = '';
  while (n > 0n) {
    const rem = Number(n % 58n);
    out = BASE58_ALPHABET[rem] + out;
    n = n / 58n;
  }
  // preserve leading zero bytes as '1'
  for (const b of buf) {
    if (b === 0) out = '1' + out;
    else break;
  }
  return out;
}

function generatePlaintextKey(): string {
  const raw = randomBytes(32);
  return `aip_${encodeBase58(raw)}`;
}

function sha256Hex(input: string): string {
  return createHash('sha256').update(input, 'utf-8').digest('hex');
}

function pickWhitelist(entity: ApiKey): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of AUDIT_WHITELIST) {
    out[k] = entity[k] as unknown;
  }
  return out;
}

@Injectable()
export class ApiKeysService {
  constructor(
    @InjectRepository(ApiKey)
    private readonly keyRepo: Repository<ApiKey>,
    @InjectRepository(ApiKeyAuditLog)
    private readonly auditRepo: Repository<ApiKeyAuditLog>,
    private readonly dataSource: DataSource,
  ) {}

  // ---- Queries ----

  async list(): Promise<ApiKeyResponseDto[]> {
    const items = await this.keyRepo.find({ order: { createdAt: 'DESC' } });
    return items.map((i) => this.toResponse(i));
  }

  async findOne(id: string): Promise<ApiKeyResponseDto> {
    const entity = await this.keyRepo.findOne({ where: { id } });
    if (!entity) throw new NotFoundException(`API key ${id} not found`);
    return this.toResponse(entity);
  }

  async getAudit(id: string, limit = 50): Promise<ApiKeyAuditEntryDto[]> {
    const items = await this.auditRepo.find({
      where: { apiKeyId: id },
      order: { createdAt: 'DESC' },
      take: Math.min(limit, 500),
    });
    return items.map((i) => ({
      id: i.id,
      api_key_id: i.apiKeyId,
      actor: i.actor,
      action: i.action,
      before: i.before,
      after: i.after,
      created_at: i.createdAt.toISOString(),
    }));
  }

  // ---- Mutations ----

  async create(
    dto: CreateApiKeyDto,
    actor: string,
  ): Promise<ApiKeyCreateResponseDto> {
    const plaintext = generatePlaintextKey();
    const keyHash = sha256Hex(plaintext);
    if (keyHash.length !== 64) {
      throw new InternalServerErrorException('key_hash length invariant failed');
    }

    const entity = this.keyRepo.create({
      keyHash,
      name: dto.name,
      allowedProfiles: dto.allowed_profiles,
      rateLimitPerMin: dto.rate_limit_per_min,
      rateLimitPerDay: dto.rate_limit_per_day,
      securityLevelMax: dto.security_level_max,
      expiresAt: dto.expires_at ? new Date(dto.expires_at) : null,
      isActive: true,
      userId: '',
      userRole: 'VIEWER',
    });
    const saved = await this.keyRepo.save(entity);

    await this.appendAudit(saved.id, actor, 'create', null, pickWhitelist(saved));

    return {
      ...this.toResponse(saved),
      plaintext_key: plaintext,
    };
  }

  async update(
    id: string,
    dto: UpdateApiKeyDto,
    actor: string,
  ): Promise<ApiKeyResponseDto> {
    const existing = await this.keyRepo.findOne({ where: { id } });
    if (!existing) throw new NotFoundException(`API key ${id} not found`);
    const before = pickWhitelist(existing);

    if (dto.name !== undefined) existing.name = dto.name;
    if (dto.allowed_profiles !== undefined)
      existing.allowedProfiles = dto.allowed_profiles;
    if (dto.rate_limit_per_min !== undefined)
      existing.rateLimitPerMin = dto.rate_limit_per_min;
    if (dto.rate_limit_per_day !== undefined)
      existing.rateLimitPerDay = dto.rate_limit_per_day;
    if (dto.security_level_max !== undefined)
      existing.securityLevelMax = dto.security_level_max;
    if (dto.expires_at !== undefined)
      existing.expiresAt = dto.expires_at ? new Date(dto.expires_at) : null;
    if (dto.is_active !== undefined) existing.isActive = dto.is_active;

    const saved = await this.keyRepo.save(existing);
    await this.appendAudit(saved.id, actor, 'update', before, pickWhitelist(saved));
    return this.toResponse(saved);
  }

  async revoke(id: string, actor: string): Promise<ApiKeyResponseDto> {
    const existing = await this.keyRepo.findOne({ where: { id } });
    if (!existing) throw new NotFoundException(`API key ${id} not found`);
    if (!existing.isActive && existing.revokedAt) {
      throw new BadRequestException('Already revoked');
    }
    const before = pickWhitelist(existing);
    existing.isActive = false;
    existing.revokedAt = new Date();
    const saved = await this.keyRepo.save(existing);
    await this.appendAudit(saved.id, actor, 'revoke', before, pickWhitelist(saved));
    return this.toResponse(saved);
  }

  async rotate(id: string, actor: string): Promise<ApiKeyCreateResponseDto> {
    // 단일 트랜잭션: 기존 revoke + 신규 create (rotated_from_id)
    const result = await this.dataSource.transaction(async (em) => {
      const oldKey = await em.findOne(ApiKey, { where: { id } });
      if (!oldKey) throw new NotFoundException(`API key ${id} not found`);
      if (!oldKey.isActive) throw new BadRequestException('Cannot rotate inactive key');

      const oldBefore = pickWhitelist(oldKey);
      oldKey.isActive = false;
      oldKey.revokedAt = new Date();
      await em.save(oldKey);

      const plaintext = generatePlaintextKey();
      const keyHash = sha256Hex(plaintext);
      if (keyHash.length !== 64) {
        throw new InternalServerErrorException('key_hash length invariant failed');
      }

      const newKey = em.create(ApiKey, {
        keyHash,
        name: oldKey.name,
        userId: oldKey.userId,
        userRole: oldKey.userRole,
        securityLevelMax: oldKey.securityLevelMax,
        allowedProfiles: oldKey.allowedProfiles,
        rateLimitPerMin: oldKey.rateLimitPerMin,
        rateLimitPerDay: oldKey.rateLimitPerDay,
        expiresAt: oldKey.expiresAt,
        rotatedFromId: oldKey.id,
        isActive: true,
      });
      const savedNew = await em.save(newKey);

      await em.save(em.create(ApiKeyAuditLog, {
        apiKeyId: oldKey.id,
        actor,
        action: 'rotate_source',
        before: oldBefore,
        after: pickWhitelist(oldKey),
      }));
      await em.save(em.create(ApiKeyAuditLog, {
        apiKeyId: savedNew.id,
        actor,
        action: 'rotate_target',
        before: null,
        after: pickWhitelist(savedNew),
      }));

      return { savedNew, plaintext };
    });

    return {
      ...this.toResponse(result.savedNew),
      plaintext_key: result.plaintext,
    };
  }

  // ---- Internals ----

  private async appendAudit(
    apiKeyId: string,
    actor: string,
    action: ApiKeyAuditLog['action'],
    before: Record<string, unknown> | null,
    after: Record<string, unknown> | null,
  ): Promise<void> {
    try {
      await this.auditRepo.save(
        this.auditRepo.create({ apiKeyId, actor, action, before, after }),
      );
    } catch (err) {
      // audit 실패는 로그만 남기고 요청은 계속
      // eslint-disable-next-line no-console
      console.warn('[api-keys] audit write failed', err);
    }
  }

  private toResponse(entity: ApiKey): ApiKeyResponseDto {
    return {
      id: entity.id,
      name: entity.name,
      preview: `aip_****${entity.keyHash.slice(-8)}`,
      allowed_profiles: entity.allowedProfiles ?? [],
      rate_limit_per_min: entity.rateLimitPerMin,
      rate_limit_per_day: entity.rateLimitPerDay,
      security_level_max: entity.securityLevelMax,
      is_active: entity.isActive,
      expires_at: entity.expiresAt ? entity.expiresAt.toISOString() : null,
      revoked_at: entity.revokedAt ? entity.revokedAt.toISOString() : null,
      rotated_from_id: entity.rotatedFromId,
      created_at: entity.createdAt.toISOString(),
      last_used_at: entity.lastUsedAt ? entity.lastUsedAt.toISOString() : null,
    };
  }
}
